from __future__ import annotations

import json
import os
import re
import base64
import hashlib
import secrets
import sys
import uuid
from dataclasses import dataclass, asdict
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from datetime import datetime, timezone

from storage import Storage


ROOT = Path(__file__).resolve().parent
GENERATED = ROOT / "generated"
DATA = ROOT / "data"
INSTANCES = DATA / "instances"
STORAGE = Storage(ROOT)
DEFAULT_BASE_URL = "https://your-creatio-site.com"
DEFAULT_VIEWER_URL = "http://127.0.0.1:8090"
BPMCSRF_SESSIONS: dict[str, dict[str, str]] = {}
IGNORED_DIRS = {
    ".git",
    ".vs",
    "bin",
    "obj",
    "node_modules",
    "packages",
    "TestResults",
}


@dataclass
class CsFile:
    name: str
    path: str
    relativePath: str
    packageName: str
    contentHash: str


@dataclass
class Package:
    name: str
    path: str
    fileCount: int


@dataclass
class Endpoint:
    method: str
    path: str
    className: str
    methodName: str
    returnType: str
    parameters: list[dict[str, str]]
    sourceFile: str


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def normalize_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if normalized.lower().endswith("/0"):
        normalized = normalized[:-2].rstrip("/")
    return normalized or DEFAULT_BASE_URL


def normalize_auth_settings(
    auth_mode: str, oauth_token_url: str
) -> tuple[str, str]:
    mode = auth_mode.strip().lower() or "bpmcsrf"
    if mode not in {"bpmcsrf", "oauth"}:
        raise ValueError("Invalid authentication mode.")
    token_url = oauth_token_url.strip()
    if mode == "oauth":
        if not token_url:
            raise ValueError("OAuth Token URL is required.")
        if not re.match(r"^https?://", token_url, re.IGNORECASE):
            raise ValueError("OAuth Token URL must use http:// or https://.")
    return mode, token_url


def find_matching_brace(text: str, start: int) -> int:
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return -1


def split_parameters(parameters: str) -> list[str]:
    result: list[str] = []
    current: list[str] = []
    depth = 0
    for char in parameters:
        if char in "(<[":
            depth += 1
        elif char in ")>]":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            item = "".join(current).strip()
            if item:
                result.append(item)
            current = []
        else:
            current.append(char)
    item = "".join(current).strip()
    if item:
        result.append(item)
    return result


def clean_type(type_name: str) -> str:
    cleaned = re.sub(r"\b(async|static|virtual|override|new|readonly)\b", "", type_name)
    cleaned = " ".join(cleaned.replace("?", "").split())
    return cleaned.strip()


def parse_parameter(parameter: str) -> dict[str, str] | None:
    parameter = re.sub(r"\[[^\]]+\]", "", parameter).strip()
    parameter = re.sub(r"\s*=\s*.+$", "", parameter).strip()
    parts = parameter.split()
    if len(parts) < 2:
        return None
    name = parts[-1].strip()
    type_name = clean_type(" ".join(parts[:-1]).replace("ref ", "").replace("out ", ""))
    return {"name": name, "type": type_name}


def csharp_type_to_schema(type_name: str, schemas: dict[str, Any] | None = None) -> dict[str, Any]:
    schemas = schemas or {}
    type_name = clean_type(type_name)
    lower = type_name.lower()

    nullable_match = re.match(r"Nullable<(.+)>", type_name)
    if nullable_match:
        schema = csharp_type_to_schema(nullable_match.group(1), schemas)
        schema["nullable"] = True
        return schema

    collection_match = re.match(r"(?:IEnumerable|ICollection|IList|List|Collection|HashSet)<(.+)>", type_name)
    if collection_match or lower.endswith("[]"):
        item_type = collection_match.group(1) if collection_match else type_name[:-2]
        return {"type": "array", "items": csharp_type_to_schema(item_type, schemas)}

    mapping = {
        "string": {"type": "string"},
        "guid": {"type": "string", "format": "uuid"},
        "bool": {"type": "boolean"},
        "boolean": {"type": "boolean"},
        "int": {"type": "integer", "format": "int32"},
        "int32": {"type": "integer", "format": "int32"},
        "long": {"type": "integer", "format": "int64"},
        "int64": {"type": "integer", "format": "int64"},
        "decimal": {"type": "number", "format": "decimal"},
        "double": {"type": "number", "format": "double"},
        "float": {"type": "number", "format": "float"},
        "datetime": {"type": "string", "format": "date-time"},
        "datetimeoffset": {"type": "string", "format": "date-time"},
        "void": {"type": "object"},
        "object": {"type": "object"},
    }
    if lower in mapping:
        return dict(mapping[lower])

    simple_name = type_name.split(".")[-1]
    if simple_name in schemas:
        return {"$ref": f"#/components/schemas/{simple_name}"}
    return {"type": "object", "x-csharp-type": type_name}


def extract_attribute_value(attributes: str, key: str) -> str | None:
    patterns = [
        rf"{key}\s*=\s*\"([^\"]+)\"",
        rf"{key}\s*:\s*\"([^\"]+)\"",
    ]
    for pattern in patterns:
        match = re.search(pattern, attributes, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def get_http_method(attributes: str) -> str:
    explicit = extract_attribute_value(attributes, "Method")
    if explicit:
        return explicit.upper()
    if "WebGet" in attributes:
        return "GET"
    if "WebInvoke" in attributes:
        return "POST"
    return "POST"


def get_uri_template(attributes: str) -> str | None:
    return extract_attribute_value(attributes, "UriTemplate")


def parse_dto_schemas(text: str) -> dict[str, Any]:
    schemas: dict[str, Any] = {}
    class_pattern = re.compile(
        r"(?:\[[^\]]+\]\s*)*(?:public|internal)?\s*(?:partial\s+)?class\s+(\w+)[^{]*\{",
        re.MULTILINE,
    )
    property_pattern = re.compile(
        r"(?:\[[^\]]+\]\s*)*public\s+([\w<>\[\].?,\s]+?)\s+(\w+)\s*\{\s*get\s*;\s*set\s*;\s*\}",
        re.MULTILINE,
    )

    for class_match in class_pattern.finditer(text):
        class_name = class_match.group(1)
        body_start = text.find("{", class_match.end() - 1)
        body_end = find_matching_brace(text, body_start)
        if body_start < 0 or body_end < 0:
            continue
        body = text[body_start + 1 : body_end]
        properties: dict[str, Any] = {}
        for prop_match in property_pattern.finditer(body):
            prop_type = clean_type(prop_match.group(1))
            prop_name = prop_match.group(2)
            properties[prop_name] = csharp_type_to_schema(prop_type, schemas)
        if properties:
            schemas[class_name] = {"type": "object", "properties": properties}
    return schemas


def parse_endpoints(text: str, relative_path: str) -> list[Endpoint]:
    endpoints: list[Endpoint] = []
    class_pattern = re.compile(
        r"(?P<attrs>(?:\s*\[[^\]]+\]\s*)*)(?:public|internal)?\s*(?:partial\s+)?class\s+(?P<name>\w+)[^{]*\{",
        re.MULTILINE,
    )
    method_pattern = re.compile(
        r"(?P<attrs>(?:\s*\[[^\]]+\]\s*)+)\s*public\s+(?:static\s+)?(?:async\s+)?(?P<return>[\w<>\[\].?,\s]+?)\s+(?P<name>\w+)\s*\((?P<params>[^)]*)\)",
        re.MULTILINE,
    )

    for class_match in class_pattern.finditer(text):
        class_attrs = class_match.group("attrs") or ""
        class_name = class_match.group("name")
        body_start = text.find("{", class_match.end() - 1)
        body_end = find_matching_brace(text, body_start)
        if body_start < 0 or body_end < 0:
            continue
        class_body = text[body_start + 1 : body_end]
        class_is_service = "ServiceContract" in class_attrs or "BaseService" in text[class_match.start() : body_start]

        for method_match in method_pattern.finditer(class_body):
            attrs = method_match.group("attrs") or ""
            if not class_is_service and not any(key in attrs for key in ("OperationContract", "WebInvoke", "WebGet")):
                continue
            if not any(key in attrs for key in ("OperationContract", "WebInvoke", "WebGet")):
                continue

            method_name = method_match.group("name")
            http_method = get_http_method(attrs)
            uri_template = get_uri_template(attrs) or method_name
            uri_template = uri_template.strip("/")
            path = f"/0/rest/{class_name}/{uri_template}".replace("//", "/")
            params = [
                parsed
                for parsed in (parse_parameter(item) for item in split_parameters(method_match.group("params")))
                if parsed
            ]
            endpoints.append(
                Endpoint(
                    method=http_method,
                    path=path,
                    className=class_name,
                    methodName=method_name,
                    returnType=clean_type(method_match.group("return")),
                    parameters=params,
                    sourceFile=relative_path,
                )
            )
    return endpoints


def should_ignore(path: Path) -> bool:
    return any(part in IGNORED_DIRS for part in path.parts)


def find_package_roots(project_path: Path, package_prefix: str) -> list[Path]:
    prefix = package_prefix.strip().lower()
    if not prefix:
        return [project_path]

    candidates = [path for path in project_path.iterdir() if path.is_dir() and path.name.lower().startswith(prefix)]
    if candidates:
        return sorted(candidates, key=lambda item: item.name.lower())

    pkg_path = project_path / "Pkg"
    if pkg_path.is_dir():
        candidates = [path for path in pkg_path.iterdir() if path.is_dir() and path.name.lower().startswith(prefix)]
        if candidates:
            return sorted(candidates, key=lambda item: item.name.lower())

    return []


def package_name_for(project_path: Path, package_roots: list[Path], file_path: Path) -> str:
    for package_root in package_roots:
        try:
            file_path.relative_to(package_root)
            if package_root == project_path:
                return file_path.relative_to(project_path).parts[0]
            return package_root.name
        except ValueError:
            continue
    return ""


def scan_cs_files(project_path: Path, package_prefix: str = "") -> tuple[list[CsFile], list[Endpoint], dict[str, Any], list[Package]]:
    files: list[CsFile] = []
    endpoints: list[Endpoint] = []
    schemas: dict[str, Any] = {}
    packages: dict[str, Package] = {}
    package_roots = find_package_roots(project_path, package_prefix)

    for package_root in package_roots:
        for file_path in package_root.rglob("*.cs"):
            if should_ignore(file_path.relative_to(project_path)):
                continue
            relative_path = str(file_path.relative_to(project_path)).replace("\\", "/")
            package_name = package_name_for(project_path, package_roots, file_path)
            if package_name:
                package = packages.setdefault(
                    package_name,
                    Package(package_name, str((project_path / package_name) if package_root == project_path else package_root), 0),
                )
                package.fileCount += 1
            try:
                text = normalize_newlines(file_path.read_text(encoding="utf-8-sig"))
            except UnicodeDecodeError:
                text = normalize_newlines(file_path.read_text(encoding="latin-1"))
            files.append(
                CsFile(
                    file_path.name,
                    str(file_path),
                    relative_path,
                    package_name,
                    hashlib.sha256(text.encode("utf-8")).hexdigest(),
                )
            )
            schemas.update(parse_dto_schemas(text))
            endpoints.extend(parse_endpoints(text, relative_path))

    return files, endpoints, schemas, sorted(packages.values(), key=lambda item: item.name.lower())


def scan_all_cs_files(project_path: Path) -> tuple[list[CsFile], list[Endpoint], dict[str, Any]]:
    files: list[CsFile] = []
    endpoints: list[Endpoint] = []
    schemas: dict[str, Any] = {}

    for file_path in project_path.rglob("*.cs"):
        if should_ignore(file_path.relative_to(project_path)):
            continue
        relative_path = str(file_path.relative_to(project_path)).replace("\\", "/")
        try:
            text = normalize_newlines(file_path.read_text(encoding="utf-8-sig"))
        except UnicodeDecodeError:
            text = normalize_newlines(file_path.read_text(encoding="latin-1"))
        files.append(
            CsFile(
                file_path.name,
                str(file_path),
                relative_path,
                "",
                hashlib.sha256(text.encode("utf-8")).hexdigest(),
            )
        )
        schemas.update(parse_dto_schemas(text))
        endpoints.extend(parse_endpoints(text, relative_path))

    return files, endpoints, schemas


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "creatio-api"


def read_catalog() -> dict[str, Any]:
    items = []
    for item in STORAGE.read_catalog().get("items", []):
        items.append(
            {
                key: value
                for key, value in item.items()
                if key not in {"viewerUsername", "viewerPassword", "viewerUrl"}
            }
        )
    return {"items": items}


def write_catalog_item(item: dict[str, Any]) -> None:
    sanitized = {
        key: value
        for key, value in item.items()
        if key not in {"viewerUsername", "viewerPassword", "viewerUrl"}
    }
    STORAGE.write_catalog_item(sanitized)


def scrub_legacy_viewer_credentials() -> None:
    for item in STORAGE.read_catalog().get("items", []):
        if any(key in item for key in ("viewerUsername", "viewerPassword", "viewerUrl")):
            write_catalog_item(item)


def find_catalog_item(slug: str) -> dict[str, Any] | None:
    for item in STORAGE.read_catalog().get("items", []):
        if item.get("slug") == slug:
            return item
    return None


def save_instance_settings(payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name", "")).strip()
    slug = str(payload.get("slug", "")).strip() or slugify(name)
    project_path = str(payload.get("projectPath", "")).strip()
    base_url = normalize_base_url(str(payload.get("baseUrl", DEFAULT_BASE_URL)))
    package_prefix = str(payload.get("packagePrefix", "")).strip()
    auth_mode, oauth_token_url = normalize_auth_settings(
        str(payload.get("authMode", "bpmcsrf")),
        str(payload.get("oauthTokenUrl", "")),
    )

    if not name:
        raise ValueError("Instance name is required.")
    if not package_prefix:
        raise ValueError("Custom package prefix is required.")
    if project_path.lower() in {"none", "null", "undefined"}:
        project_path = ""

    existing = find_catalog_item(slug) or {}
    item = {
        **existing,
        "name": name,
        "slug": slug,
        "url": existing.get("url", f"/docs/{slug}/openapi.json"),
        "projectPath": project_path,
        "baseUrl": base_url,
        "packagePrefix": package_prefix,
        "authMode": auth_mode,
        "oauthTokenUrl": oauth_token_url if auth_mode == "oauth" else "",
        "fileCount": existing.get("fileCount", 0),
        "packageCount": existing.get("packageCount", 0),
        "endpointCount": existing.get("endpointCount", 0),
        "generatedAt": existing.get("generatedAt"),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    write_catalog_item(item)
    return item


def read_viewer_settings() -> dict[str, Any]:
    settings = STORAGE.read_settings()
    if settings:
        return settings

    # Migrate legacy per-instance viewer credentials on first use.
    for item in STORAGE.read_catalog().get("items", []):
        if item.get("viewerUrl") or item.get("viewerUsername"):
            settings = {
                "viewerUrl": item.get("viewerUrl") or DEFAULT_VIEWER_URL,
                "viewerUsername": item.get("viewerUsername") or "",
                "viewerPassword": item.get("viewerPassword") or "",
                "updatedAt": datetime.now(timezone.utc).isoformat(),
            }
            STORAGE.write_settings(settings)
            return settings
    return {
        "viewerUrl": DEFAULT_VIEWER_URL,
        "viewerUsername": "",
        "viewerPassword": "",
    }


def public_viewer_settings() -> dict[str, Any]:
    settings = read_viewer_settings()
    return {
        "viewerUrl": settings.get("viewerUrl", DEFAULT_VIEWER_URL),
        "scannerName": settings.get("scannerName", ""),
        "installationId": settings.get("installationId", ""),
        "registrationStatus": settings.get("registrationStatus", "not_registered"),
        "updatedAt": settings.get("updatedAt"),
    }


def save_viewer_settings(payload: dict[str, Any]) -> dict[str, Any]:
    existing = read_viewer_settings()
    viewer_url = str(payload.get("viewerUrl", "")).strip().rstrip("/")
    scanner_name = str(payload.get("scannerName", "")).strip()
    if not viewer_url:
        raise ValueError("Viewer service URL is required.")
    if not re.match(r"^https?://", viewer_url, re.IGNORECASE):
        raise ValueError("Viewer service URL must use http:// or https://.")
    if not scanner_name:
        raise ValueError("Scanner name is required.")
    installation_id = str(existing.get("installationId") or uuid.uuid4())
    scanner_token = str(existing.get("scannerToken") or secrets.token_urlsafe(48))
    settings = {
        "viewerUrl": viewer_url,
        "scannerName": scanner_name,
        "installationId": installation_id,
        "scannerToken": scanner_token,
        "registrationStatus": existing.get("registrationStatus", "pending"),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    registration = register_scanner(settings)
    settings["registrationStatus"] = registration.get("status", "pending")
    STORAGE.write_settings(settings)
    scrub_legacy_viewer_credentials()
    return public_viewer_settings()


def build_openapi(
    base_url: str,
    endpoints: list[Endpoint],
    schemas: dict[str, Any],
    title: str = "Creatio API Documentation",
    auth_mode: str = "bpmcsrf",
    oauth_token_url: str = "",
    document_slug: str = "",
) -> dict[str, Any]:
    base_url = normalize_base_url(base_url)
    auth_mode, oauth_token_url = normalize_auth_settings(
        auth_mode, oauth_token_url
    )
    if auth_mode == "oauth":
        security_schemes = {
            "oauth2ClientCredentials": {
                "type": "oauth2",
                "description": "OAuth 2.0 Client Credentials.",
                "flows": {
                    "clientCredentials": {
                        "tokenUrl": oauth_token_url,
                        "scopes": {},
                    }
                },
            }
        }
        operation_security = [{"oauth2ClientCredentials": []}]
    else:
        security_schemes = {
            "creatioBasicAuth": {
                "type": "http",
                "scheme": "basic",
                "description": (
                    "Enter your Creatio username and password. The scanner proxy "
                    "logs in to Creatio, stores the BPMCSRF session cookie, and "
                    "uses that cookie for service requests."
                ),
            }
        }
        operation_security = [{"creatioBasicAuth": []}]
    openapi: dict[str, Any] = {
        "openapi": "3.0.3",
        "info": {
            "title": title,
            "description": "API documentation generated from scanned Creatio C# source code.",
            "version": "1.0.0",
        },
        "servers": [{"url": base_url, "description": "Creatio"}],
        "tags": [],
        "paths": {},
        "components": {
            "securitySchemes": security_schemes,
            "schemas": schemas,
        },
        "x-authentication-mode": auth_mode,
    }

    tag_names = sorted({endpoint.className for endpoint in endpoints})
    openapi["tags"] = [
        {
            "name": "Authentication",
            "description": "Endpoints used to obtain a session or access token.",
        },
        *[
            {"name": tag, "description": f"Endpoints provided by {tag}."}
            for tag in tag_names
        ],
    ]

    if auth_mode == "oauth":
        token_parts = urlsplit(oauth_token_url)
        token_server = f"{token_parts.scheme}://{token_parts.netloc}"
        token_path = token_parts.path or "/connect/token"
        openapi["paths"][token_path] = {
            "post": {
                "tags": ["Authentication"],
                "summary": "OAuth Client Credentials",
                "description": "Obtain an access token using OAuth 2.0 Client Credentials.",
                "operationId": "Authentication_OAuthClientCredentials",
                "servers": [{"url": token_server, "description": "OAuth Server"}],
                "security": [],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/x-www-form-urlencoded": {
                            "schema": {
                                "type": "object",
                                "required": [
                                    "grant_type",
                                    "client_id",
                                    "client_secret",
                                ],
                                "properties": {
                                    "grant_type": {
                                        "type": "string",
                                        "default": "client_credentials",
                                        "example": "client_credentials",
                                    },
                                    "client_id": {
                                        "type": "string",
                                        "example": "YOUR_CLIENT_ID",
                                    },
                                    "client_secret": {
                                        "type": "string",
                                        "format": "password",
                                        "example": "YOUR_CLIENT_SECRET",
                                    },
                                },
                            }
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Access token created successfully.",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "access_token": {"type": "string"},
                                        "expires_in": {
                                            "type": "integer",
                                            "format": "int32",
                                        },
                                        "token_type": {"type": "string"},
                                        "scope": {"type": "string"},
                                    },
                                },
                                "example": {
                                    "access_token": "eyJhbGciOiJSUzI1NiIs...ACCESS_TOKEN",
                                    "expires_in": 3600,
                                    "token_type": "Bearer",
                                    "scope": "ApplicationAccess_your_scope",
                                },
                            }
                        },
                    },
                    "400": {"description": "Invalid OAuth request."},
                    "401": {"description": "Invalid client credentials."},
                },
            }
        }
    else:
        login_path = "/ServiceModel/AuthService.svc/Login"
        openapi["paths"][login_path] = {
            "post": {
                "tags": ["Authentication"],
                "summary": "Login Creatio",
                "description": (
                    "Create a Creatio session and obtain the BPMCSRF cookie. "
                    "The scanner proxy accepts Username and Password, then sends "
                    "them to Creatio as UserName and UserPassword."
                ),
                "operationId": "Authentication_CreatioLogin",
                "security": [],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["Username", "Password"],
                                "properties": {
                                    "Username": {
                                        "type": "string",
                                        "example": "{{Username}}",
                                    },
                                    "Password": {
                                        "type": "string",
                                        "format": "password",
                                        "example": "{{Password}}",
                                    },
                                },
                            },
                            "example": {
                                "Username": "{{Username}}",
                                "Password": "{{Password}}",
                            },
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Login successful.",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "Code": {"type": "integer"},
                                        "Message": {"type": "string"},
                                        "Exception": {
                                            "type": "string",
                                            "nullable": True,
                                        },
                                        "PasswordChangeUrl": {
                                            "type": "string",
                                            "nullable": True,
                                        },
                                        "RedirectUrl": {"type": "string"},
                                        "UserType": {"type": "string"},
                                    },
                                },
                                "example": {
                                    "Code": 0,
                                    "Message": "",
                                    "Exception": None,
                                    "PasswordChangeUrl": None,
                                    "RedirectUrl": "/0/Shell",
                                    "UserType": "General",
                                },
                            }
                        },
                    },
                    "401": {"description": "Invalid username or password."},
                },
            }
        }

    for endpoint in endpoints:
        parameters = []
        request_properties = {}
        for param in endpoint.parameters:
            schema = csharp_type_to_schema(param["type"], schemas)
            if endpoint.method == "GET":
                parameters.append(
                    {
                        "name": param["name"],
                        "in": "query",
                        "required": False,
                        "schema": schema,
                    }
                )
            else:
                request_properties[param["name"]] = schema

        operation: dict[str, Any] = {
            "tags": [endpoint.className],
            "summary": endpoint.methodName,
            "operationId": f"{endpoint.className}_{endpoint.methodName}",
            "security": operation_security,
            "parameters": parameters,
            "responses": {
                "200": {
                    "description": "Success",
                    "content": {
                        "application/json": {
                            "schema": csharp_type_to_schema(endpoint.returnType, schemas)
                        }
                    },
                },
                "401": {"description": "Unauthorized"},
            },
            "x-source-file": endpoint.sourceFile,
        }

        if request_properties:
            operation["requestBody"] = {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": request_properties,
                        }
                    }
                },
            }

        path_item = openapi["paths"].setdefault(endpoint.path, {})
        path_item[endpoint.method.lower()] = operation

    return openapi


def normalize_bpmcsrf_openapi(openapi: dict[str, Any]) -> dict[str, Any]:
    if openapi.get("x-authentication-mode", "bpmcsrf") != "bpmcsrf":
        return openapi

    components = openapi.setdefault("components", {})
    security_schemes = components.setdefault("securitySchemes", {})
    if "creatioCookieAuth" in security_schemes or "creatioBasicAuth" not in security_schemes:
        security_schemes.pop("creatioCookieAuth", None)
        security_schemes["creatioBasicAuth"] = {
            "type": "http",
            "scheme": "basic",
            "description": (
                "Enter your Creatio username and password. The scanner proxy "
                "logs in to Creatio, stores the BPMCSRF session cookie, and "
                "uses that cookie for service requests."
            ),
        }

    for path_item in openapi.get("paths", {}).values():
        if not isinstance(path_item, dict):
            continue
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            security = operation.get("security")
            if security == [{"creatioCookieAuth": []}]:
                operation["security"] = [{"creatioBasicAuth": []}]

    login = openapi.get("paths", {}).get("/ServiceModel/AuthService.svc/Login", {}).get("post")
    if isinstance(login, dict):
        login["description"] = (
            "Create a Creatio session and obtain the BPMCSRF cookie. "
            "The scanner proxy accepts Username and Password, then sends "
            "them to Creatio as UserName and UserPassword."
        )
        json_body = (
            login.setdefault("requestBody", {})
            .setdefault("content", {})
            .setdefault("application/json", {})
        )
        json_body["schema"] = {
            "type": "object",
            "required": ["Username", "Password"],
            "properties": {
                "Username": {"type": "string", "example": "{{Username}}"},
                "Password": {
                    "type": "string",
                    "format": "password",
                    "example": "{{Password}}",
                },
            },
        }
        json_body["example"] = {
            "Username": "{{Username}}",
            "Password": "{{Password}}",
        }
    return openapi


def write_generated(openapi: dict[str, Any], result: dict[str, Any]) -> None:
    GENERATED.mkdir(exist_ok=True)
    (GENERATED / "openapi.json").write_text(json.dumps(openapi, indent=2), encoding="utf-8")
    (GENERATED / "scan-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")


def write_instance_scan(slug: str, openapi: dict[str, Any], result: dict[str, Any]) -> str:
    return STORAGE.write_document(slug, openapi, result)


def oauth_token_url_for_instance(slug: str) -> str:
    item = find_catalog_item(slug)
    if not item or item.get("authMode") != "oauth":
        raise ValueError("OAuth is not configured for this instance.")
    token_url = str(item.get("oauthTokenUrl", "")).strip()
    if not token_url:
        raise ValueError("OAuth Token URL has not been configured.")
    return token_url


def bpmcsrf_base_url_for_instance(slug: str) -> str:
    item = find_catalog_item(slug)
    if not item or item.get("authMode", "bpmcsrf") != "bpmcsrf":
        raise ValueError("BPMCSRF is not configured for this instance.")
    return normalize_base_url(str(item.get("baseUrl", DEFAULT_BASE_URL)))


def update_bpmcsrf_cookies(slug: str, headers: Any) -> None:
    session = BPMCSRF_SESSIONS.setdefault(slug, {})
    for header in headers.get_all("Set-Cookie", []):
        cookie = SimpleCookie()
        cookie.load(header)
        for name, morsel in cookie.items():
            session[name] = morsel.value


def bpmcsrf_cookie_header(slug: str) -> str:
    session = BPMCSRF_SESSIONS.get(slug, {})
    return "; ".join(f"{name}={value}" for name, value in session.items())


def bpmcsrf_token(slug: str) -> str:
    session = BPMCSRF_SESSIONS.get(slug, {})
    for name, value in session.items():
        if name.lower() == "bpmcsrf":
            return value
    return ""


def basic_auth_credentials(authorization: str) -> tuple[str, str] | None:
    if not authorization.startswith("Basic "):
        return None
    try:
        decoded = base64.b64decode(
            authorization.removeprefix("Basic ").strip()
        ).decode("utf-8")
    except Exception:
        return None
    username, separator, password = decoded.partition(":")
    if not separator or not username or not password:
        return None
    return username, password


def compare_scan_results(
    previous: dict[str, Any] | None, current: dict[str, Any]
) -> dict[str, Any]:
    previous_files = {
        item.get("relativePath"): item.get("contentHash")
        for item in (previous or {}).get("files", [])
        if item.get("relativePath")
    }
    current_files = {
        item.get("relativePath"): item.get("contentHash")
        for item in current.get("files", [])
        if item.get("relativePath")
    }
    previous_endpoints = {
        (item.get("method"), item.get("path"), item.get("sourceFile"))
        for item in (previous or {}).get("endpoints", [])
    }
    current_endpoints = {
        (item.get("method"), item.get("path"), item.get("sourceFile"))
        for item in current.get("endpoints", [])
    }

    added_files = sorted(set(current_files) - set(previous_files))
    removed_files = sorted(set(previous_files) - set(current_files))
    modified_files = sorted(
        path
        for path in set(previous_files) & set(current_files)
        if previous_files[path] != current_files[path]
    )
    added_endpoints = sorted(current_endpoints - previous_endpoints)
    removed_endpoints = sorted(previous_endpoints - current_endpoints)
    previous_openapi = (previous or {}).get("openapi", {})
    current_openapi = current.get("openapi", {})
    previous_auth = {
        "mode": previous_openapi.get("x-authentication-mode", "bpmcsrf"),
        "schemes": previous_openapi.get("components", {}).get(
            "securitySchemes", {}
        ),
    }
    current_auth = {
        "mode": current_openapi.get("x-authentication-mode", "bpmcsrf"),
        "schemes": current_openapi.get("components", {}).get(
            "securitySchemes", {}
        ),
    }
    authentication_changed = previous_auth != current_auth
    initial_scan = previous is None or not previous.get("files")
    has_changes = bool(
        added_files
        or removed_files
        or modified_files
        or added_endpoints
        or removed_endpoints
        or authentication_changed
    )
    return {
        "initialScan": initial_scan,
        "hasChanges": has_changes,
        "addedFiles": added_files,
        "removedFiles": removed_files,
        "modifiedFiles": modified_files,
        "addedEndpointCount": len(added_endpoints),
        "removedEndpointCount": len(removed_endpoints),
        "authenticationChanged": authentication_changed,
    }


def viewer_request(
    viewer_url: str,
    token: str,
    path: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not viewer_url:
        raise ValueError("Viewer URL is required.")
    if not token:
        raise ValueError("The scanner is not registered with Viewer.")
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        f"{viewer_url.rstrip('/')}{path}",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8")
        if exc.code == 401:
            raise PermissionError("The scanner is not registered with Viewer.") from exc
        raise ValueError(f"Viewer menolak request: {detail}") from exc
    except URLError as exc:
        raise ValueError(f"Viewer could not be reached: {exc.reason}") from exc


def register_scanner(settings: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(
        {
            "scannerId": settings["installationId"],
            "name": settings["scannerName"],
            "token": settings["scannerToken"],
        }
    ).encode("utf-8")
    request = Request(
        f"{settings['viewerUrl'].rstrip('/')}/api/scanners/register",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise ValueError(f"Scanner registration was rejected: {exc.read().decode('utf-8')}") from exc
    except URLError as exc:
        raise ValueError(f"Viewer could not be reached: {exc.reason}") from exc


def test_viewer_connection(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    current = {**read_viewer_settings(), **(settings or {})}
    viewer_url = str(current.get("viewerUrl", "")).strip().rstrip("/")
    try:
        result = viewer_request(
            viewer_url,
            str(current.get("scannerToken", "")),
            "/api/scanner/status",
        )
    except PermissionError:
        reset_scanner_registration(current)
        return {
            "status": "not_registered",
            "service": "Creatio API Viewer",
            "registrationReset": True,
        }
    current["registrationStatus"] = result.get("status", "pending")
    STORAGE.write_settings(current)
    return result


def remote_documents() -> dict[str, Any]:
    settings = read_viewer_settings()
    try:
        return viewer_request(
            str(settings.get("viewerUrl", "")),
            str(settings.get("scannerToken", "")),
            "/api/scanner/documents",
        )
    except PermissionError:
        reset_scanner_registration(settings)
        raise ValueError(
            "The scanner is no longer registered with Viewer. Register again through Settings."
        )


def reset_scanner_registration(settings: dict[str, Any]) -> None:
    reset = {
        "viewerUrl": settings.get("viewerUrl", DEFAULT_VIEWER_URL),
        "scannerName": settings.get("scannerName", ""),
        "registrationStatus": "not_registered",
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    STORAGE.write_settings(reset)


def sync_with_viewer() -> dict[str, Any]:
    remote = remote_documents()
    remote_items = {
        item.get("slug"): item
        for item in remote.get("items", [])
        if item.get("slug")
    }
    synced_at = datetime.now(timezone.utc).isoformat()
    updated_items: list[dict[str, Any]] = []

    for item in STORAGE.read_catalog().get("items", []):
        remote_item = remote_items.get(item.get("slug"))
        if remote_item:
            updated = {
                **item,
                "publishStatus": "published",
                "publishedUrl": remote_item.get("viewerPage")
                or item.get("publishedUrl"),
                "publishedAt": remote_item.get("publishedAt")
                or item.get("publishedAt"),
                "lastSyncedAt": synced_at,
            }
        else:
            was_published = bool(
                item.get("publishedAt")
                or item.get("publishedUrl")
                or item.get("publishStatus") in {"published", "removed"}
            )
            updated = {
                **item,
                "publishStatus": "removed" if was_published else "not_published",
                "lastSyncedAt": synced_at,
            }
            if was_published:
                updated["lastPublishedUrl"] = item.get("publishedUrl")
                updated.pop("publishedUrl", None)
        write_catalog_item(updated)
        updated_items.append(updated)

    settings = read_viewer_settings()
    settings["lastSyncedAt"] = synced_at
    STORAGE.write_settings(settings)
    return {
        "catalog": read_catalog(),
        "lastSyncedAt": synced_at,
        "remoteCount": len(remote_items),
    }


def delete_remote_document(slug: str) -> dict[str, Any]:
    settings = read_viewer_settings()
    return viewer_request(
        str(settings.get("viewerUrl", "")),
        str(settings.get("scannerToken", "")),
        f"/api/scanner/documents/{slug}",
        "DELETE",
    )


def publish_instance(slug: str) -> dict[str, Any]:
    item = find_catalog_item(slug)
    if not item:
        raise ValueError("Instance not found.")

    openapi = STORAGE.read_document(slug)
    if openapi is None:
        raise ValueError("This instance has no scan results. Run a scan first.")
    openapi = normalize_bpmcsrf_openapi(openapi)

    settings = read_viewer_settings()
    viewer_url = settings.get("viewerUrl") or DEFAULT_VIEWER_URL
    token = settings.get("scannerToken") or ""
    payload = {
        "name": item["name"],
        "slug": item["slug"],
        "openapi": openapi,
        "metadata": {
            "baseUrl": item.get("baseUrl"),
            "packagePrefix": item.get("packagePrefix"),
            "authMode": item.get("authMode", "bpmcsrf"),
            "oauthTokenUrl": item.get("oauthTokenUrl", ""),
            "fileCount": item.get("fileCount", 0),
            "packageCount": item.get("packageCount", 0),
            "endpointCount": item.get("endpointCount", 0),
            "generatedAt": item.get("generatedAt"),
        },
    }
    response = viewer_request(viewer_url, token, "/api/publish", "POST", payload)
    published_url = response.get("url") or f"{viewer_url.rstrip('/')}/?doc={slug}"
    item = {
        **item,
        "publishStatus": "published",
        "publishedUrl": published_url,
        "publishedAt": datetime.now(timezone.utc).isoformat(),
        "lastSyncedAt": datetime.now(timezone.utc).isoformat(),
    }
    write_catalog_item(item)
    return {"item": item, "viewer": response}


def _is_dir_safe(path: Path) -> bool:
    try:
        return path.is_dir()
    except (PermissionError, OSError):
        return False


def get_browse_root() -> Path:
    configured = os.environ.get("BROWSE_ROOT", "").strip()
    if os.name == "nt" and configured == "__drives__":
        return Path("__drives__")
    if configured:
        candidate = Path(configured).expanduser()
        if _is_dir_safe(candidate):
            return candidate.resolve()

    if os.name == "nt":
        return Path("__drives__")
    else:
        candidate = Path("/Users") if sys.platform == "darwin" else Path("/home")

    if _is_dir_safe(candidate):
        return candidate.resolve()
    return Path.home().resolve()


def windows_drives() -> list[str]:
    if os.name != "nt":
        return []
    return [
        f"{letter}:"
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        if _is_dir_safe(Path(f"{letter}:\\"))
    ]


def windows_drive_target(relative_path: str) -> tuple[Path, Path] | None:
    normalized = relative_path.replace("\\", "/").strip("/")
    if not normalized:
        return None

    drive_name, _, remainder = normalized.partition("/")
    if not re.fullmatch(r"[A-Za-z]:", drive_name):
        raise ValueError("Path tidak diizinkan.")

    drive_name = drive_name.upper()
    if drive_name not in windows_drives():
        raise FileNotFoundError("Drive tidak ditemukan.")

    drive_root = Path(f"{drive_name}\\").resolve()
    target = (drive_root / remainder).resolve() if remainder else drive_root
    try:
        target.relative_to(drive_root)
    except ValueError as exc:
        raise ValueError("Path tidak diizinkan.") from exc
    return drive_root, target


def _host_display_path(target: Path, browse_root: Path) -> str:
    browse_root_env = os.environ.get("BROWSE_ROOT", "/host")
    host_browse_root = os.environ.get("HOST_BROWSE_ROOT", "").rstrip("/\\")
    # Dalam Docker, BROWSE_ROOT=/host dan HOST_BROWSE_ROOT=path asli di host.
    # Native: BROWSE_ROOT sudah path asli, tidak perlu translasi.
    display_root = host_browse_root if host_browse_root and browse_root_env == "/host" else browse_root_env.rstrip("/\\")
    sep = "\\" if len(display_root) >= 2 and display_root[1] == ":" else "/"
    if target == browse_root:
        return display_root or sep
    rel = str(target.relative_to(browse_root))
    if sep == "\\":
        rel = rel.replace("/", "\\")
    return display_root + sep + rel


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def proxy_oauth_token(self, slug: str) -> None:
        try:
            token_url = oauth_token_url_for_instance(slug)
            content_type = self.headers.get("Content-Type", "")
            if not content_type.lower().startswith(
                "application/x-www-form-urlencoded"
            ):
                self.send_json(
                    400,
                    {"error": "The OAuth token request must be form-urlencoded."},
                )
                return
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            form_data = dict(
                parse_qsl(body.decode("utf-8"), keep_blank_values=True)
            )
            authorization = self.headers.get("Authorization", "")
            if authorization.startswith("Basic "):
                try:
                    decoded = base64.b64decode(
                        authorization.removeprefix("Basic ").strip()
                    ).decode("utf-8")
                    client_id, _, client_secret = decoded.partition(":")
                    form_data.setdefault("client_id", client_id)
                    form_data.setdefault("client_secret", client_secret)
                except Exception:
                    self.send_json(
                        400, {"error": "Invalid OAuth client credentials."}
                    )
                    return
            body = urlencode(form_data).encode("utf-8")
            request = Request(
                token_url,
                data=body,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                method="POST",
            )
            try:
                with urlopen(request, timeout=30) as response:
                    response_body = response.read()
                    status = response.status
                    response_type = response.headers.get(
                        "Content-Type", "application/json"
                    )
            except HTTPError as exc:
                response_body = exc.read()
                status = exc.code
                response_type = exc.headers.get(
                    "Content-Type", "application/json"
                )
            self.send_response(status)
            self.send_header("Content-Type", response_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)
        except (ValueError, URLError) as exc:
            self.send_json(502, {"error": str(exc)})

    def proxy_bpmcsrf_request(
        self, slug: str, proxied_path: str, method: str
    ) -> None:
        try:
            base_url = bpmcsrf_base_url_for_instance(slug)
            proxied_path = proxied_path or "/"
            is_login = proxied_path.lower() == "/servicemodel/authservice.svc/login"
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else None
            content_type = self.headers.get("Content-Type", "")
            authorization = self.headers.get("Authorization", "")
            basic_credentials = basic_auth_credentials(authorization)
            if is_login:
                if not content_type.lower().startswith("application/json"):
                    self.send_json(
                        400,
                        {"error": "The BPMCSRF login request must be JSON."},
                    )
                    return
                credentials = json.loads(body or b"{}")
                username = credentials.get("Username", credentials.get("UserName"))
                password = credentials.get("Password", credentials.get("UserPassword"))
                if not username or not password:
                    self.send_json(
                        400,
                        {"error": "Username and Password are required."},
                    )
                    return
                body = json.dumps(
                    {"UserName": username, "UserPassword": password}
                ).encode("utf-8")
            elif not BPMCSRF_SESSIONS.get(slug):
                if not basic_credentials:
                    self.send_json(
                        401,
                        {"error": "Enter your Creatio username and password through Authorize before executing this service."},
                    )
                    return
                username, password = basic_credentials
                login_body = json.dumps(
                    {"UserName": username, "UserPassword": password}
                ).encode("utf-8")
                login_request = Request(
                    f"{base_url}/ServiceModel/AuthService.svc/Login",
                    data=login_body,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                try:
                    with urlopen(login_request, timeout=30) as login_response:
                        login_response.read()
                        update_bpmcsrf_cookies(slug, login_response.headers)
                except HTTPError as exc:
                    detail = exc.read()
                    self.send_response(exc.code)
                    self.send_header(
                        "Content-Type",
                        exc.headers.get("Content-Type", "application/json"),
                    )
                    self.send_header("Content-Length", str(len(detail)))
                    self.end_headers()
                    self.wfile.write(detail)
                    return
                if not BPMCSRF_SESSIONS.get(slug):
                    self.send_json(
                        401,
                        {"error": "Creatio login did not return a BPMCSRF session cookie."},
                    )
                    return

            query = ""
            if "?" in self.path:
                query = self.path.split("?", 1)[1]
            base_parts = urlsplit(base_url)
            target_path = (
                f"{base_parts.path.rstrip('/')}{proxied_path}"
                if base_parts.path.rstrip("/")
                else proxied_path
            )
            target_url = urlunsplit((
                base_parts.scheme,
                base_parts.netloc,
                target_path,
                query,
                "",
            ))
            headers = {
                "Accept": self.headers.get("Accept", "application/json"),
            }
            if body is not None and content_type:
                headers["Content-Type"] = content_type
            cookie_header = bpmcsrf_cookie_header(slug)
            if cookie_header:
                headers["Cookie"] = cookie_header
            token = bpmcsrf_token(slug)
            if token:
                headers["BPMCSRF"] = token

            request = Request(
                target_url,
                data=None if method in {"GET", "HEAD"} else body,
                headers=headers,
                method=method,
            )
            try:
                with urlopen(request, timeout=30) as response:
                    response_body = response.read()
                    status = response.status
                    response_headers = response.headers
            except HTTPError as exc:
                response_body = exc.read()
                status = exc.code
                response_headers = exc.headers

            update_bpmcsrf_cookies(slug, response_headers)
            self.send_response(status)
            self.send_header(
                "Content-Type",
                response_headers.get("Content-Type", "application/json"),
            )
            self.send_header("Cache-Control", "no-store")
            csrf = bpmcsrf_token(slug)
            if csrf:
                self.send_header("X-BPMCSRF", csrf)
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            if method != "HEAD":
                self.wfile.write(response_body)
        except json.JSONDecodeError:
            self.send_json(400, {"error": "Invalid JSON login request."})
        except (ValueError, URLError) as exc:
            self.send_json(502, {"error": str(exc)})

    def end_headers(self) -> None:
        path = self.path.split("?", 1)[0]
        if path.endswith((".html", ".js", ".css")) or path == "/":
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        super().end_headers()

    def do_GET(self) -> None:
        path = unquote(self.path.split("?", 1)[0])
        bpmcsrf_match = re.fullmatch(r"/api/bpmcsrf/proxy/([^/]+)(/.*)?", path)
        if bpmcsrf_match:
            self.proxy_bpmcsrf_request(
                bpmcsrf_match.group(1), bpmcsrf_match.group(2) or "/", "GET"
            )
            return
        if path == "/api/browse":
            qs = dict(parse_qsl(self.path.split("?", 1)[1]) if "?" in self.path else [])
            browse_root = get_browse_root()
            rel = qs.get("path", "").strip("/")
            drive_mode = os.name == "nt" and str(browse_root) == "__drives__"

            if drive_mode and not rel:
                self.send_json(200, {
                    "current": "",
                    "parent": None,
                    "fullPath": "",
                    "hostPath": "This PC",
                    "rootLabel": "This PC",
                    "entries": windows_drives(),
                })
                return

            try:
                if drive_mode:
                    drive_result = windows_drive_target(rel)
                    if drive_result is None:
                        raise ValueError("Path tidak diizinkan.")
                    allowed_root, target = drive_result
                else:
                    allowed_root = browse_root
                    target = (browse_root / rel).resolve() if rel else browse_root
                    target.relative_to(allowed_root)
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
                return
            except FileNotFoundError as exc:
                self.send_json(404, {"error": str(exc)})
                return

            if not target.is_dir():
                self.send_json(404, {"error": "Direktori tidak ditemukan."})
                return
            try:
                entries = sorted(
                    [
                        e.name for e in target.iterdir()
                        if not e.name.startswith(".")
                        and _is_dir_safe(e)
                    ],
                    key=str.lower,
                )
            except PermissionError:
                self.send_json(403, {"error": "Akses ditolak."})
                return

            if drive_mode:
                drive_prefix = f"{allowed_root.drive.upper()}"
                relative = target.relative_to(allowed_root)
                current = drive_prefix if str(relative) == "." else (
                    drive_prefix + "/" + relative.as_posix()
                )
                parent = "" if target == allowed_root else current.rsplit("/", 1)[0]
                full_path = str(target)
                host_path = str(target)
                root_label = "This PC"
            else:
                relative = target.relative_to(browse_root)
                current = "" if target == browse_root else relative.as_posix()
                parent_path = relative.parent
                parent = None if target == browse_root else (
                    "" if str(parent_path) == "." else parent_path.as_posix()
                )
                full_path = str(target)
                host_path = _host_display_path(target, browse_root)
                root_label = str(browse_root)

            self.send_json(200, {
                "current": current,
                "parent": parent,
                "fullPath": full_path,
                "hostPath": host_path,
                "rootLabel": root_label,
                "entries": entries,
            })
            return
        if path == "/api/instances":
            self.send_json(200, read_catalog())
            return
        if path == "/api/settings":
            self.send_json(200, public_viewer_settings())
            return
        if path == "/api/remote-documents":
            try:
                self.send_json(200, remote_documents())
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
            return
        document_match = re.fullmatch(r"/api/instances/([^/]+)/openapi", path)
        if document_match:
            document = STORAGE.read_document(document_match.group(1))
            if document is None:
                self.send_json(404, {"error": "Documentation not found."})
                return
            self.send_json(200, normalize_bpmcsrf_openapi(document))
            return
        result_match = re.fullmatch(r"/api/instances/([^/]+)/scan-result", path)
        if result_match:
            result = STORAGE.read_scan_result(result_match.group(1))
            if result is None:
                self.send_json(404, {"error": "Scan results not found."})
                return
            if isinstance(result.get("openapi"), dict):
                result["openapi"] = normalize_bpmcsrf_openapi(result["openapi"])
            self.send_json(200, result)
            return
        super().do_GET()

    def do_POST(self) -> None:
        path = unquote(self.path.split("?", 1)[0])
        oauth_match = re.fullmatch(r"/api/oauth/token/([^/]+)", path)
        if oauth_match:
            self.proxy_oauth_token(oauth_match.group(1))
            return
        bpmcsrf_match = re.fullmatch(r"/api/bpmcsrf/proxy/([^/]+)(/.*)?", path)
        if bpmcsrf_match:
            self.proxy_bpmcsrf_request(
                bpmcsrf_match.group(1), bpmcsrf_match.group(2) or "/", "POST"
            )
            return
        if path not in {
            "/api/scan",
            "/api/instances/save",
            "/api/publish",
            "/api/settings",
            "/api/settings/test",
            "/api/sync",
        }:
            self.send_json(404, {"error": "Endpoint not found."})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            if path == "/api/publish":
                try:
                    result = publish_instance(str(payload.get("slug", "")).strip())
                except ValueError as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, {"result": result, "catalog": read_catalog()})
                return

            if path == "/api/sync":
                try:
                    result = sync_with_viewer()
                except ValueError as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, result)
                return

            if path == "/api/settings":
                try:
                    settings = save_viewer_settings(payload)
                except ValueError as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, settings)
                return

            if path == "/api/settings/test":
                try:
                    result = test_viewer_connection()
                except ValueError as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, result)
                return

            if path == "/api/instances/save":
                try:
                    item = save_instance_settings(payload)
                except ValueError as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, {"item": item, "catalog": read_catalog()})
                return

            doc_name = str(payload.get("docName", "")).strip()
            project_path = Path(str(payload.get("path", "")).strip()).expanduser()
            base_url = normalize_base_url(
                str(payload.get("baseUrl", DEFAULT_BASE_URL))
            )
            package_prefix = str(payload.get("packagePrefix", "")).strip()
            try:
                auth_mode, oauth_token_url = normalize_auth_settings(
                    str(payload.get("authMode", "bpmcsrf")),
                    str(payload.get("oauthTokenUrl", "")),
                )
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
                return

            if not doc_name:
                self.send_json(400, {"error": "Documentation name is required."})
                return
            if not project_path.exists() or not project_path.is_dir():
                self.send_json(400, {"error": "The project path was not found or is not a directory."})
                return
            if not package_prefix:
                self.send_json(400, {"error": "Custom package prefix is required."})
                return

            files, endpoints, schemas, packages = scan_cs_files(project_path, package_prefix)
            slug = slugify(doc_name)
            openapi = build_openapi(
                base_url,
                endpoints,
                schemas,
                doc_name,
                auth_mode,
                oauth_token_url,
                slug,
            )
            previous_result = STORAGE.read_scan_result(slug)
            generated_at = datetime.now(timezone.utc).isoformat()
            catalog_item = {
                **(find_catalog_item(slug) or {}),
                "name": doc_name,
                "slug": slug,
                "url": f"/docs/{slug}/openapi.json",
                "projectPath": str(project_path),
                "baseUrl": base_url,
                "packagePrefix": package_prefix,
                "authMode": auth_mode,
                "oauthTokenUrl": oauth_token_url if auth_mode == "oauth" else "",
                "fileCount": len(files),
                "packageCount": len(packages),
                "endpointCount": len(endpoints),
                "generatedAt": generated_at,
            }
            serialized_files = [asdict(file) for file in files]
            serialized_packages = [asdict(package) for package in packages]
            serialized_endpoints = [asdict(endpoint) for endpoint in endpoints]
            result = {
                "files": serialized_files,
                "packages": serialized_packages,
                "endpoints": serialized_endpoints,
                "openapi": openapi,
                "catalogItem": catalog_item,
                "filters": {"packagePrefix": package_prefix},
            }
            result["changeSummary"] = compare_scan_results(previous_result, result)
            result["localOpenApiUrl"] = f"/api/instances/{slug}/openapi"
            write_instance_scan(slug, openapi, result)
            write_generated(openapi, result)
            write_catalog_item(catalog_item)
            self.send_json(200, result)
        except Exception as exc:
            self.send_json(500, {"error": str(exc)})

    def do_PUT(self) -> None:
        path = unquote(self.path.split("?", 1)[0])
        bpmcsrf_match = re.fullmatch(r"/api/bpmcsrf/proxy/([^/]+)(/.*)?", path)
        if not bpmcsrf_match:
            self.send_json(404, {"error": "Endpoint not found."})
            return
        self.proxy_bpmcsrf_request(
            bpmcsrf_match.group(1), bpmcsrf_match.group(2) or "/", "PUT"
        )

    def do_PATCH(self) -> None:
        path = unquote(self.path.split("?", 1)[0])
        bpmcsrf_match = re.fullmatch(r"/api/bpmcsrf/proxy/([^/]+)(/.*)?", path)
        if not bpmcsrf_match:
            self.send_json(404, {"error": "Endpoint not found."})
            return
        self.proxy_bpmcsrf_request(
            bpmcsrf_match.group(1), bpmcsrf_match.group(2) or "/", "PATCH"
        )

    def do_DELETE(self) -> None:
        path = unquote(self.path.split("?", 1)[0])
        bpmcsrf_match = re.fullmatch(r"/api/bpmcsrf/proxy/([^/]+)(/.*)?", path)
        if bpmcsrf_match:
            self.proxy_bpmcsrf_request(
                bpmcsrf_match.group(1), bpmcsrf_match.group(2) or "/", "DELETE"
            )
            return
        match = re.fullmatch(r"/api/instances/([^/]+)", path)
        if not match:
            self.send_json(404, {"error": "Endpoint not found."})
            return
        try:
            slug = match.group(1)
            delete_remote = self.headers.get("X-Delete-Remote", "").lower() == "true"
            if find_catalog_item(slug) is None:
                self.send_json(404, {"error": "Instance not found."})
                return
            if delete_remote:
                try:
                    delete_remote_document(slug)
                except ValueError as exc:
                    self.send_json(
                        400,
                        {
                            "error": (
                                f"Failed to delete the Viewer documentation. "
                                f"Local data was preserved. {exc}"
                            )
                        },
                    )
                    return
            STORAGE.delete_instance(slug)
            self.send_json(
                200,
                {
                    "ok": True,
                    "remoteDeleted": delete_remote,
                    "catalog": read_catalog(),
                },
            )
        except Exception as exc:
            self.send_json(500, {"error": str(exc)})


def main() -> None:
    GENERATED.mkdir(exist_ok=True)
    fallback = build_openapi(DEFAULT_BASE_URL, [], {}, "Creatio API Documentation")
    if not (GENERATED / "openapi.json").exists():
        write_generated(fallback, {"files": [], "endpoints": [], "openapi": fallback})
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Creatio API Scanner running at http://{host}:{port}")
    print(f"Storage backend: {STORAGE.backend}")
    server.serve_forever()


if __name__ == "__main__":
    main()
