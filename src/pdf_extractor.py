'''
Add Blob Storage PDF extraction utility

Pulls PDFs from a given container and extracts their text.
Requires AZURE_STORAGE_CONNECTION_STRING in .env (get from Key Vault).
Container names are passed as function args, not hardcoded — replace
"software-engineering-docs"/"company-docs" with real container names
wherever this is called.
'''

import os
from io import BytesIO
from azure.storage.blob import BlobServiceClient
from pypdf import PdfReader
from dotenv import load_dotenv

load_dotenv()

AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")


def get_blob_service_client():
    """
    Creates a client for connecting to Azure Blob Storage.
    """
    return BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)


def list_pdfs_in_container(container_name: str) -> list:
    """
    Lists all blob names (file names) in a given container.
    """
    client = get_blob_service_client()
    container_client = client.get_container_client(container_name)
    return [blob.name for blob in container_client.list_blobs() if blob.name.endswith(".pdf")]


def extract_text_from_blob_pdf(container_name: str, blob_name: str) -> str:
    """
    Downloads a single PDF from Blob Storage (into memory, not disk)
    and extracts its text.
    """
    client = get_blob_service_client()
    container_client = client.get_container_client(container_name)
    blob_client = container_client.get_blob_client(blob_name)

    pdf_bytes = blob_client.download_blob().readall()
    reader = PdfReader(BytesIO(pdf_bytes))

    text = ""
    for page in reader.pages:
        text += page.extract_text() + "\n"
    return text


def extract_text_from_container(container_name: str) -> str:
    """
    Extracts and combines text from every PDF in a given container
    (e.g. all docs for one role/category), ready to feed into
    the content generation agent.
    """
    pdf_names = list_pdfs_in_container(container_name)
    combined_text = ""
    for name in pdf_names:
        combined_text += extract_text_from_blob_pdf(container_name, name) + "\n\n"
    return combined_text