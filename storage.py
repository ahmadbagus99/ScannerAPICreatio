from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class Storage:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.data = root / "data"
        self.instances = self.data / "instances"
        self.backend = os.environ.get("STORAGE_BACKEND", "json").strip().lower()
        if self.backend not in {"json", "postgres"}:
            raise RuntimeError("STORAGE_BACKEND must be either 'json' or 'postgres'.")
        if self.backend == "postgres":
            self._init_postgres()
        else:
            self._init_json()

    def _connect(self):
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError(
                "PostgreSQL mode requires the psycopg dependency. "
                "Run: pip install -r requirements.txt"
            ) from exc

        database_url = os.environ.get("DATABASE_URL", "").strip()
        if not database_url:
            raise RuntimeError("DATABASE_URL is required when STORAGE_BACKEND=postgres.")
        return psycopg.connect(database_url)

    def _init_json(self) -> None:
        self.data.mkdir(exist_ok=True)
        self.instances.mkdir(exist_ok=True)
        path = self.data / "catalog.json"
        if not path.exists():
            path.write_text(json.dumps({"items": []}, indent=2), encoding="utf-8")
        settings_path = self.data / "settings.json"
        if not settings_path.exists():
            settings_path.write_text(json.dumps({}, indent=2), encoding="utf-8")

    def _init_postgres(self) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS scanner_instances (
                        slug TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        payload JSONB NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS scanner_settings (
                        setting_key TEXT PRIMARY KEY,
                        payload JSONB NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS scanner_documents (
                        slug TEXT PRIMARY KEY,
                        openapi JSONB NOT NULL,
                        scan_result JSONB,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )

    def read_catalog(self) -> dict[str, Any]:
        if self.backend == "json":
            path = self.data / "catalog.json"
            try:
                return json.loads(path.read_text(encoding="utf-8-sig"))
            except (FileNotFoundError, json.JSONDecodeError):
                return {"items": []}

        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT payload FROM scanner_instances ORDER BY LOWER(name)")
                return {"items": [row[0] for row in cursor.fetchall()]}

    def write_catalog_item(self, item: dict[str, Any]) -> None:
        if self.backend == "json":
            catalog = self.read_catalog()
            items = [
                existing
                for existing in catalog.get("items", [])
                if existing.get("slug") != item["slug"]
            ]
            items.append(item)
            items.sort(key=lambda existing: existing.get("name", "").lower())
            (self.data / "catalog.json").write_text(
                json.dumps({"items": items}, indent=2), encoding="utf-8"
            )
            return

        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO scanner_instances (slug, name, payload)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (slug) DO UPDATE SET
                        name = EXCLUDED.name,
                        payload = EXCLUDED.payload,
                        updated_at = NOW()
                    """,
                    (item["slug"], item["name"], json.dumps(item)),
                )

    def read_settings(self) -> dict[str, Any]:
        if self.backend == "json":
            path = self.data / "settings.json"
            try:
                return json.loads(path.read_text(encoding="utf-8-sig"))
            except (FileNotFoundError, json.JSONDecodeError):
                return {}

        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT payload FROM scanner_settings WHERE setting_key = %s",
                    ("viewer_integration",),
                )
                row = cursor.fetchone()
                return row[0] if row else {}

    def write_settings(self, settings: dict[str, Any]) -> None:
        if self.backend == "json":
            (self.data / "settings.json").write_text(
                json.dumps(settings, indent=2), encoding="utf-8"
            )
            return

        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO scanner_settings (setting_key, payload)
                    VALUES (%s, %s)
                    ON CONFLICT (setting_key) DO UPDATE SET
                        payload = EXCLUDED.payload,
                        updated_at = NOW()
                    """,
                    ("viewer_integration", json.dumps(settings)),
                )

    def write_document(
        self, slug: str, openapi: dict[str, Any], scan_result: dict[str, Any]
    ) -> str:
        if self.backend == "json":
            instance_dir = self.instances / slug
            instance_dir.mkdir(parents=True, exist_ok=True)
            (instance_dir / "openapi.json").write_text(
                json.dumps(openapi, indent=2), encoding="utf-8"
            )
            (instance_dir / "scan-result.json").write_text(
                json.dumps(scan_result, indent=2), encoding="utf-8"
            )
        else:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO scanner_documents (slug, openapi, scan_result)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (slug) DO UPDATE SET
                            openapi = EXCLUDED.openapi,
                            scan_result = EXCLUDED.scan_result,
                            updated_at = NOW()
                        """,
                        (slug, json.dumps(openapi), json.dumps(scan_result)),
                    )
        return f"/api/instances/{slug}/openapi"

    def read_document(self, slug: str) -> dict[str, Any] | None:
        if self.backend == "json":
            path = self.instances / slug / "openapi.json"
            if not path.exists():
                return None
            return json.loads(path.read_text(encoding="utf-8"))

        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT openapi FROM scanner_documents WHERE slug = %s", (slug,)
                )
                row = cursor.fetchone()
                return row[0] if row else None

    def read_scan_result(self, slug: str) -> dict[str, Any] | None:
        if self.backend == "json":
            path = self.instances / slug / "scan-result.json"
            if not path.exists():
                return None
            try:
                return json.loads(path.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError:
                return None

        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT scan_result FROM scanner_documents WHERE slug = %s",
                    (slug,),
                )
                row = cursor.fetchone()
                return row[0] if row else None

    def delete_instance(self, slug: str) -> bool:
        if self.backend == "json":
            catalog = self.read_catalog()
            existing = next(
                (item for item in catalog.get("items", []) if item.get("slug") == slug),
                None,
            )
            if existing is None:
                return False
            items = [
                item for item in catalog.get("items", []) if item.get("slug") != slug
            ]
            (self.data / "catalog.json").write_text(
                json.dumps({"items": items}, indent=2), encoding="utf-8"
            )
            instance_dir = self.instances / slug
            for filename in ("openapi.json", "scan-result.json"):
                path = instance_dir / filename
                if path.exists():
                    path.unlink()
            if instance_dir.exists() and not any(instance_dir.iterdir()):
                instance_dir.rmdir()
            return True

        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM scanner_documents WHERE slug = %s", (slug,))
                cursor.execute(
                    "DELETE FROM scanner_instances WHERE slug = %s RETURNING slug",
                    (slug,),
                )
                return cursor.fetchone() is not None
