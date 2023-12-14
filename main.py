# Copyright 2021 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the 'License');
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an 'AS IS' BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# [START functions_cloudevent_pubsub]
import base64
import json
import google.auth.transport.requests
import google.auth
from cloudevents.http import CloudEvent
from google.cloud import resourcemanager_v3
import os
import functions_framework
from google.cloud import pubsub_v1
import urllib
import logging
import google.cloud.logging
from google.cloud import datastore
from datetime import datetime, timedelta, timezone
import uuid


# Instantiates a client
logging_client = google.cloud.logging.Client()

# Retrieves a Cloud Logging handler based on the environment
# you're running in and integrates the handler with the
# Python logging module. By default this captures all logs
# at INFO level and higher
logging_client.setup_logging()


# TODO(developer): set this environment variables
PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT")
TOPIC_NAME = os.environ.get("TOPIC_NAME")

# Constants
MAX_RESOURCE_TTL_DAYS = 2

# Class definition to Resource persistence on Firestore
class GoogleResource:
    def __init__(self, resourceId, resourceName,resourceType, resourceParent, ttl):
        self.resourceId = resourceId
        self.resourceName = resourceName
        self.resourceType = resourceType
        self.resourceParent = resourceParent
        self.ttl = ttl

def persistResource(GoogleResource):
    datastore_client = datastore.Client()
    key = datastore_client.key('Resource', GoogleResource.resourceId)
    entity = datastore.Entity(key=key)
    logging.info("Persisting Resource {} on Firestore".format(GoogleResource.resourceId))
    entity.update(GoogleResource.__dict__)
    try:
        datastore_client.put(entity)
        logging.info("Resource {} persisted on Firestore".format(GoogleResource.resourceId))
    except Exception as e:
        logging.error("Error persist on Firestore: {}".format(e))

def queryResource(resourceId):
    datastore_client = datastore.Client()
    query = datastore_client.query(kind='Resource')
    try:
        query.fetch()
        for entity in query.fetch():
            if entity['resourceId'] == resourceId:
                difference = datetime.now(timezone.utc) - entity["ttl"]
                max_age = timedelta(days=MAX_RESOURCE_TTL_DAYS)
                if difference < max_age:
                    logging.info("Resource {} found on Firestore".format(resourceId))
                    return entity
                else:
                    logging.info("Resource {} found on Firestore but expired".format(resourceId))
                    return None
        logging.info("Resource {} not found on Firestore".format(resourceId))
        return None
    except Exception as e:
        logging.error("Error querying on Firestore: {}".format(e))
        return None

# Publishes a message to a Cloud Pub/Sub topic.
def publish(request):
    # Instantiates a Pub/Sub client
    publisher = pubsub_v1.PublisherClient()
    # References an existing topic
    topic_path = publisher.topic_path(PROJECT_ID, TOPIC_NAME)

    message_json = json.dumps(
        {
            "data": {"message": request},
        }
    )
    message_bytes = message_json.encode("utf-8")

    # Publishes a message
    try:
        publish_future = publisher.publish(topic_path, data=message_bytes)
        publish_future.result()  # Verify the publish succeeded
        logging.info("Message published on {} topic".format(TOPIC_NAME))
        return ("Message published.", 200)
    except Exception as e:
        logging.error("Error Publishing the message: {}".format(e))
        return (e, 500)
    
def getBillingAccount(logName):
 
    msgarray = logName.split("/",2)
    # Setting the billing v1 api audience to get credetials accordlying
    audience = "https://cloudbilling.googleapis.com/"
    # Setting the endpoint REST API to GET billing account information
    # https://cloud.google.com/billing/docs/reference/rest/v1/billingAccounts/get
    endpoint = "https://cloudbilling.googleapis.com/v1/billingAccounts/{}".format(msgarray[1])
    # Setting the request to the endpoint
    req = urllib.request.Request(endpoint)
    # getting the credentials and project details for gcp project
    # adding OAuth Scopes to call the billing REST API
    scopes = [
        "https://www.googleapis.com/auth/cloud-billing.readonly"
        ]
    credentials, project_id = google.auth.default(scopes=scopes)
    auth_req = google.auth.transport.requests.Request()
    #Getting the access token
    credentials.refresh(auth_req)
    #Include the Bearer token into the Authorization Header
    req.add_header("Authorization", f"Bearer {credentials.token}")
    try:
        # # Make the request
        # # needs roles/roles/billing.viewer permission to function Service account
        # # at Billing level
        response = urllib.request.urlopen(req)
        responsejson = json.loads(response.read().decode('utf-8'))
        # The Billing API has the 'parent' element as an undocumented field in
        # the response Object
        # used as-is as this field can be removed at any time
        # include try/except to handle gracefully if the parameter have been removed
        orgarray = responsejson['parent'].split("/",2)
        billingName = responsejson['displayName']
        orgid = orgarray[1]
        return  orgid, billingName
    except Exception as e:
        logging.error("Error getting Billing Account details: {}".format(e))
        return "N/A", "N/A"

def getFolder(logName):
    # Create a client
    folder_client = resourcemanager_v3.FoldersClient()
    msgarray = logName.split("/",2)
    # Initialize request argument(s)
    request = resourcemanager_v3.GetFolderRequest(
        name="{}/{}".format(msgarray[0], msgarray[1])
        )
    # Make the request
    # needs roles/resourcemanager.folderAdmin permission to function Service account
    # at folder/org level 
    try:
        response = folder_client.get_folder(request=request)
        orgarray = response.parent.split("/",2)
        folderName = response.display_name
        orgid = orgarray[1]
    except:
        logging.error("Error getting Folder details: {}".format(e))
        return "N/A","N/A"
    return orgid, folderName

def getProject(logName):
    # Create a client
    msgarray = logName.split("/",2)
    project_client = resourcemanager_v3.ProjectsClient()
    # Initialize request argument(s)
    request = resourcemanager_v3.GetProjectRequest(
        name="{}/{}".format(msgarray[0], msgarray[1]),
    )
    # Make the request
    try:
        response = project_client.get_project(request=request)
        orgarray = response.parent.split("/",2)
        projectName = response.display_name
        orgid = orgarray[1]
    except:
        logging.error("Error getting Project details: {}".format(e))
        return "N/A","N/A"
    return orgid, projectName

def getOrganizationName(orgid):
     # Create a client
    org_client = resourcemanager_v3.OrganizationsClient()
    # Initialize request argument(s)
    request = resourcemanager_v3.GetOrganizationRequest(
        name="organizations/{}".format(orgid)
    )

    # Make the request
    # needs resourcemanager.organizations.get permission to function Service account
    # at org level 
    try:
        response = org_client.get_organization(request=request)
        orgName = response.display_name
        orgOwner = response.directory_customer_id
    except Exception as e:
        logging.error("Error getting OrgName: {}".format(e))
        orgName = "N/A"
    return orgName, orgOwner

# Triggered from a message on a Cloud Pub/Sub topic.
@functions_framework.cloud_event
def subscribe(cloud_event: CloudEvent) -> None:
    orgName = None
    orgid = None
    org_resource = None
    msg = json.loads(base64.b64decode(cloud_event.data["message"]["data"]).decode())
    msgarray = msg["logName"].split("/",2)
    logging.info("Processing audit log for {}".format(msgarray[0]),extra={"json_fields": msg})
    # checking the scope of the audit log message
    # possible values are: project, organization, billingAccount and 
    logging.info("Checking firestore if resource is already persisted")
    query = queryResource(msgarray[1]) 
    if query is not None:
        if msgarray[0] == "organizations":
            orgid = query["resourceId"]
        else:    
            orgid = query['resourceParent']
    else:
        if msgarray[0] == "organizations":
            orgid = msgarray[1]
            orgName, orgOwner = getOrganizationName(orgid)
            org_resource = GoogleResource(orgid,orgName,msgarray[0],orgOwner,datetime.now(timezone.utc))
        if msgarray[0] == "billingAccounts":
            orgid, resourceName = getBillingAccount(msg["logName"])
        if msgarray[0] == "folders":
            orgid, resourceName = getFolder(msg["logName"])
        if msgarray[0] == "projects":
            orgid, resourceName = getProject(msg["logName"])
        # persisting dicovered resource on firestore to offload API calls
        resource = GoogleResource(msgarray[1],resourceName,msgarray[0],orgid,datetime.now())
        persistResource(resource)
    if orgName is None:  
        query = queryResource(orgid)
        if query is not None:
            orgName = query['resourceName']
        else:
            # persisting dicovered organization on firestore to offload API calls
            orgName, orgOwner = getOrganizationName(orgid)
            if org_resource is None:
                org_resource = GoogleResource(orgid,orgName,"organizations",orgOwner,datetime.now())
            persistResource(org_resource)
    #updating the audit log json before publish to pubsup
    msg["orgName"] = orgName
    msg["orgId"] = orgid
    logging.info("Enriched audit log message",extra={"json_fields": msg})
    # publishing to pubsub to be consumed to sentinel
    publish(msg)
    