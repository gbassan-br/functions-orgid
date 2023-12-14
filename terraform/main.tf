terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 4.34.0"
    }
  }
}

variable "org" {
  type = string
  nullable = false
}

variable "project" {
  type = string
  nullable = false
}

variable "acct" {
  type = string
  nullable = false
}

variable "region" {
  type = string
  default = "us-central1"
}

# TODO remove hardcoded values to vars of sed shell automation

data "google_organization" "org" {
  domain = var.org
}

data "google_billing_account" "acct" {
  billing_account = var.acct
  open         = true
}

data "google_project" "project" {
    project_id = var.project
}

provider "google" {
    project = var.project
  
}

variable "gcp_service_list" {
  description ="The list of apis necessary for the project"
  type = list(string)
  default = [
    "cloudfunctions.googleapis.com",
    "firestore.googleapis.com",
    "datastore.googleapis.com",
    "eventarc.googleapis.com",
    "pubsub.googleapis.com",
    "logging.googleapis.com",
    "storage.googleapis.com",
    "run.googleapis.com"
  ]
}

resource "google_project_service" "gcp_services" {
  for_each = toset(var.gcp_service_list)
  project = data.google_project.project.project_id
  service = each.key
}

resource "random_id" "bucket_prefix" {
  byte_length = 8
}

resource "google_service_account" "sa-name" {
    
    account_id   = "functions-orgid-sa"
    display_name = "Service Account for functions-orgid"
}

resource "google_service_account" "sa-run-invoke" {
    
    account_id   = "pubsub-subso-invoke-sa"
    display_name = "Service Account for functions-orgid"
}

resource "google_organization_iam_member" "functions_folder_binding" {
    org_id = data.google_organization.org.org_id
    role    = "roles/resourcemanager.folderViewer"
    member  = "serviceAccount:${google_service_account.sa-name.email}"
}

resource "google_organization_iam_member" "functions_org_binding" {
    org_id = data.google_organization.org.org_id
    role    = "roles/resourcemanager.organizationViewer"
    member  = "serviceAccount:${google_service_account.sa-name.email}"
}

# this resource should be used since give the billing permissions at the right level
# for Argolis, this should be done manually on go/argolis page with your @google.com user

resource "google_billing_account_iam_member" "functions_billing_binding" {
    billing_account_id = data.google_billing_account.acct.id
    role    = "roles/billing.viewer"
    member  = "serviceAccount:${google_service_account.sa-name.email}"
}

# resource "google_organization_iam_member" "functions_billing_binding" {
#     org_id = data.google_organization.org.org_id
#     role    = "roles/billing.viewer"
#     member  = "serviceAccount:${google_service_account.sa-name.email}"
# }
resource "google_project_iam_member" "functions_publisher_binding" {
    project = data.google_project.project.project_id
    role    = "roles/pubsub.publisher"
    member  = "serviceAccount:${google_service_account.sa-name.email}"
}
resource "google_project_iam_member" "functions_subscriber_binding" {
    project = data.google_project.project.project_id   
    role    = "roles/pubsub.subscriber"
    member  = "serviceAccount:${google_service_account.sa-name.email}"
}

resource "google_project_iam_member" "functions_datastore_binding" {
    project = data.google_project.project.project_id   
    role    = "roles/datastore.user"
    member  = "serviceAccount:${google_service_account.sa-name.email}"
}
resource "google_project_iam_member" "pubsub_invoker" {
    project = data.google_project.project.project_id   
    role    = "roles/run.invoker"
    member  = "serviceAccount:${google_service_account.sa-run-invoke.email}"
}
resource "google_pubsub_topic" "out" {
    name = "sentinel_out"
}

resource "google_pubsub_topic" "pre" {
    name = "sentinel_pre"
}

resource "google_logging_project_sink" "sentinel-sink" {
    name = "sentinel-sink"

    # Can export to pubsub, cloud storage, bigquery, log bucket, or another project
    destination = "pubsub.googleapis.com/${google_pubsub_topic.pre.id}"

    # Log all WARN or higher severity messages relating to instances
    # TODO Alter to the filter 
    filter = "<FILTER>"
    #Example of audit logs related to datastore
    # filter = "resource.type=\"audited_resource\" protoPayload.serviceName=\"datastore.googleapis.com\" log_name=\"projects/${data.google_project.project.project_id}/logs/cloudaudit.googleapis.com%2Fdata_access\" "
    
    # DO NOT REMOVE this exclusion, it is required to avoid infinite loop
    exclusions {
        name        = "Resource_exclusion"
        description = "Exclusion to not consider audit logs from metadata insertion into firestore. This aims to avoid infinite loop to cloud functions"
        filter      = "resource.type=\"audited_resource\" log_name=\"projects/${data.google_project.project.project_id}/logs/cloudaudit.googleapis.com%2Fdata_access\" resource.labels.service=\"datastore.googleapis.com\" (protoPayload.request.query.kind.name=\"Resource\" OR protoPayload.request.mutations.upsert.key.path.kind=\"Resource\" OR protoPayload.request.mutations.delete.path.kind=\"Resource\" OR jsonPayload.protoPayload.request.query.filter.propertyFilter.value.keyValue.path.name=\"Resource\")"
    }

    # Use a unique writer (creates a unique service account used for writing)
    unique_writer_identity = true
    # Use a user-managed service account
}

# grant writer access to the user-managed service account
resource "google_project_iam_member" "writer_identity-binding" {
    project = data.google_project.project.project_id    
    role   = "roles/pubsub.publisher"
    member = google_logging_project_sink.sentinel-sink.writer_identity
     depends_on = [google_logging_project_sink.sentinel-sink]
}

resource "google_storage_bucket" "default" {
    name                        = "${random_id.bucket_prefix.hex}-gcf-source" # Every bucket name must be globally unique
    location                    = "US"
    uniform_bucket_level_access = true
}

data "archive_file" "default" {
    type        = "zip"
    output_path = "/tmp/function-source.zip"
    source_dir  = "../."
}

resource "google_storage_bucket_object" "default" {
    name   = "function-source.zip"
    bucket = google_storage_bucket.default.name
    source = data.archive_file.default.output_path # Path to the zipped function source code
}

resource "google_cloudfunctions2_function" "default" {
    
    name        = "functions-orgid"
    location    = var.region
    description = "function to include orgId into the audit logs"

  build_config {
        runtime     = "python312"
        entry_point = "subscribe" # Set the entry point
        environment_variables = {

    }
    source {
      storage_source {
            bucket = google_storage_bucket.default.name
            object = google_storage_bucket_object.default.name
      }
    }
  }

  service_config {
        max_instance_count = 3
        min_instance_count = 1
        available_memory   = "256M"
        timeout_seconds    = 60
        environment_variables = {
        SERVICE_CONFIG_TEST = "config_test",
        GOOGLE_CLOUD_PROJECT = data.google_project.project.project_id,
        TOPIC_NAME = "sentinel_out",
        }
        # ingress_settings               = "ALLOW_INTERNAL_ONLY"
        all_traffic_on_latest_revision = true
        service_account_email          = google_service_account.sa-name.email
  }

  event_trigger {
        trigger_region = var.region
        event_type     = "google.cloud.pubsub.topic.v1.messagePublished"
        pubsub_topic   = google_pubsub_topic.pre.id
        retry_policy   = "RETRY_POLICY_RETRY"
        service_account_email = google_service_account.sa-run-invoke.email
  }
}