#!/usr/bin/env python
"""Download a genuine public water-leakage dataset export from Roboflow Universe.

Roboflow requires an API key even for public Universe datasets. This script does not
fabricate data: if ROBOFLOW_API_KEY is absent, it stops with a clear configuration error.
"""

import argparse
import os
import sys
import zipfile
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass


def download(workspace: str, project: str, version: int, model_format: str, destination: str, api_key: str = None):
    api_key = api_key or os.getenv("ROBOFLOW_API_KEY", "")
    if not api_key:
        raise RuntimeError("ROBOFLOW_API_KEY is required to export a public Universe dataset")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    url = f"https://api.roboflow.com/{workspace}/{project}/{version}/{model_format}"
    response = requests.get(url, params={"api_key": api_key}, stream=True, timeout=120)
    if response.status_code != 200:
        raise RuntimeError(f"Roboflow export failed ({response.status_code}): {response.text[:500]}")
    content_type = response.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        payload = response.json()
        export_link = (payload.get("export") or {}).get("link")
        response.close()
        if not export_link:
            raise RuntimeError("Roboflow export JSON did not contain a download link")
        response = requests.get(export_link, stream=True, timeout=120)
        if response.status_code != 200:
            raise RuntimeError(f"Roboflow export link download failed ({response.status_code})")
    archive = destination / f"{project}-{version}.zip"
    with archive.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                handle.write(chunk)
    response.close()
    with zipfile.ZipFile(archive) as zip_file:
        zip_file.extractall(destination)
    archive.unlink(missing_ok=True)
    return {"dataset_root": str(destination), "source": url}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--version", type=int, required=True)
    parser.add_argument("--format", default="yolov8")
    parser.add_argument("--destination", required=True)
    parser.add_argument("--api-key")
    args = parser.parse_args()
    try:
        print(download(args.workspace, args.project, args.version, args.format, args.destination, args.api_key))
        return 0
    except Exception as exc:
        print(f"Dataset download failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())