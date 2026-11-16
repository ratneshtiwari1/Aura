import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from azure.core.exceptions import ClientAuthenticationError
from azure.identity import CredentialUnavailableError, DefaultAzureCredential
from fastapi import FastAPI, HTTPException

FABRIC_WORKSPACES_URL = "https://api.fabric.microsoft.com/v1/workspaces"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"

app = FastAPI(title="Power BI Automation Fabric API")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/workspaces")
def list_workspaces() -> dict:
    try:
        with DefaultAzureCredential() as credential:
            token = credential.get_token(FABRIC_SCOPE).token
        request = Request(FABRIC_WORKSPACES_URL, headers={"Authorization": f"Bearer {token}"})
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except (ClientAuthenticationError, CredentialUnavailableError) as error:
        raise HTTPException(status_code=503, detail="Fabric authentication is unavailable") from error
    except HTTPError as error:
        raise HTTPException(status_code=error.code, detail="Fabric workspace request failed") from error
    except URLError as error:
        raise HTTPException(status_code=502, detail="Could not reach the Fabric API") from error