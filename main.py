from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
import os
import requests
import tarfile
import zipfile
import io
import subprocess
from typing import List
from bs4 import BeautifulSoup
import logging

app = FastAPI()

logging.basicConfig(level=logging.INFO)

class ProjectMetadata(BaseModel):
    name: str
    version: str
    author: str
    author_email: str
    description: str
    long_description: str
    classifiers: List[str]
    python_requires: str
    packages: List[str]
    install_requires: List[str]

def get_pypi_source_url(package_name: str):
    pypi_url = f"https://pypi.org/simple/{package_name}/"
    response = requests.get(pypi_url)
    if response.status_code == 200:
        soup = BeautifulSoup(response.text, 'html.parser')
        for a in soup.find_all('a'):
            href = a.get('href')
            if href.endswith('.tar.gz') or href.endswith('.zip'):
                return href
    raise HTTPException(status_code=404, detail="Package source not found on PyPI")

def download_source(url, dest):
    response = requests.get(url)
    if response.status_code == 200:
        if url.endswith('.tar.gz'):
            tar = tarfile.open(fileobj=io.BytesIO(response.content))
            tar.extractall(path=dest)
        elif url.endswith('.zip'):
            z = zipfile.ZipFile(io.BytesIO(response.content))
            z.extractall(path=dest)
        else:
            raise HTTPException(status_code=400, detail="Unsupported file type")
    else:
        raise HTTPException(status_code=response.status_code, detail=f"Error downloading file from {url}")

@app.post("/compile-wheel")
async def compile_wheel(metadata: ProjectMetadata, background_tasks: BackgroundTasks):
    project_name = metadata.name
    project_dir = f"/mnt/data/{project_name}"
    
    # Create the project directory if it doesn't exist
    os.makedirs(project_dir, exist_ok=True)

    # Get the source URL from PyPI
    source_url = get_pypi_source_url(metadata.name)
    
    # Download the source code
    download_source(source_url, project_dir)
    
    # Create README.md if it doesn't exist
    readme_file = os.path.join(project_dir, "README.md")
    if not os.path.exists(readme_file):
        with open(readme_file, "w") as f:
            f.write(metadata.long_description)
    
    # Create setup.py using the metadata if it doesn't exist
    setup_file = os.path.join(project_dir, "setup.py")
    if not os.path.exists(setup_file):
        setup_content = f"""\
from setuptools import setup, find_packages

setup(
    name="{metadata.name}",
    version="{metadata.version}",
    packages=find_packages(),
    install_requires={metadata.install_requires},
    author="{metadata.author}",
    author_email="{metadata.author_email}",
    description="{metadata.description}",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    url="{source_url}",
    classifiers={metadata.classifiers},
    python_requires='{metadata.python_requires}',
)
"""
        with open(setup_file, "w") as f:
            f.write(setup_content)

    # Compile the wheel
    background_tasks.add_task(compile_and_build, project_dir)

    return {"message": "Compilation initiated", "project_dir": project_dir}

def compile_and_build(project_dir: str):
    os.chdir(project_dir)
    logging.info(f"Starting build process in directory: {project_dir}")
    try:
        subprocess.run(["pip", "install", "setuptools", "wheel"], check=True)
        subprocess.run(["python", "setup.py", "sdist", "bdist_wheel"], check=True)
        logging.info(f"Build process completed successfully for directory: {project_dir}")
    except subprocess.CalledProcessError as e:
        logging.error(f"Build process failed: {e}")

@app.get("/simple/", response_class=HTMLResponse)
async def list_projects():
    base_dir = "/mnt/data"
    projects = [d for d in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, d))]
    html_content = "<!DOCTYPE html><html><body>"
    for project in projects:
        html_content += f'<a href="/simple/{project}/">{project}</a><br>'
    html_content += "</body></html>"
    return html_content

@app.get("/simple/{project}/", response_class=HTMLResponse)
async def list_project_files(project: str):
    project_dir = os.path.join("/mnt/data", project, "dist")
    if not os.path.exists(project_dir):
        raise HTTPException(status_code=404, detail="Project not found")
    
    files = os.listdir(project_dir)
    html_content = "<!DOCTYPE html><html><body>"
    for file in files:
        file_path = f"/data/{project}/dist/{file}"
        html_content += f'<a href="{file_path}">{file}</a><br>'
    html_content += "</body></html>"
    return html_content

@app.get("/data/{project}/dist/{file_path:path}")
async def serve_file(project: str, file_path: str):
    full_path = os.path.join("/mnt/data", project, "dist", file_path)
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(full_path)
