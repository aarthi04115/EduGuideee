import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite+pysqlite://"
os.environ["JWT_SECRET_KEY"] = "test-only-secret-with-at-least-32-bytes"
os.environ["GROQ_API_KEY"] = "test-only-not-a-real-groq-key"

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import pymupdf

from database import Base, get_db, _normalize_database_url
from models import Document, User
from study_materials import (
    get_material,
    initialize_materials,
    remove_material,
    retrieve_context_with_sources,
)
import main


class AuthenticationHistoryTests(unittest.TestCase):
    def test_database_url_encodes_unescaped_password_at_sign(self):
        url = make_url(
            _normalize_database_url(
                "postgresql://student:pass@word@localhost:5432/eduguide_db"
            )
        )
        self.assertEqual(url.drivername, "postgresql+psycopg")
        self.assertEqual(url.host, "localhost")
        self.assertEqual(url.database, "eduguide_db")
        self.assertEqual(url.password, "pass@word")

    def test_alembic_creates_schema_from_an_empty_database(self):
        migration_engine = create_engine(
            "sqlite+pysqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        config = Config()
        config.set_main_option(
            "script_location",
            str((Path(__file__).parent / "alembic").resolve()),
        )
        try:
            with migration_engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
                config.attributes.pop("connection", None)
                tables = set(inspect(connection).get_table_names())
                self.assertTrue(
                    {
                        "users",
                        "auth_sessions",
                        "conversations",
                        "messages",
                        "documents",
                        "conversation_documents",
                        "alembic_version",
                    }.issubset(tables)
                )
                self.assertEqual(
                    connection.execute(text("SELECT count(*) FROM users")).scalar_one(),
                    0,
                )
        finally:
            migration_engine.dispose()

    def test_readiness_reports_an_unapplied_schema_migration(self):
        with self.engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE users")
        response = self.client.get("/health/ready")
        self.assertEqual(response.status_code, 503)
        self.assertIn("migration", response.json()["detail"].lower())

    def test_initialize_materials_counts_without_restoring_indexes(self):
        with self.session_factory() as db:
            db.add(
                User(
                    id="startup-user",
                    name="Startup Test",
                    email="startup@example.edu",
                    password_hash="not-used",
                )
            )
            db.add(
                Document(
                    id="startup-document",
                    user_id="startup-user",
                    filename="material.txt",
                    storage_path="missing-material.txt",
                    status="indexed",
                    file_size=10,
                )
            )
            db.commit()

            with patch(
                "study_materials.create_embeddings",
                side_effect=AssertionError("startup must not embed documents"),
            ):
                self.assertEqual(initialize_materials(db), 1)
            self.assertIsNone(get_material("startup-document", "startup-user"))

    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self.engine,
        )

        def test_database():
            db = self.session_factory()
            try:
                yield db
            finally:
                db.close()

        self.test_database = test_database
        main.app.dependency_overrides[get_db] = self.test_database
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close()
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    @staticmethod
    def csrf(client):
        response = client.get("/auth/csrf")
        if response.status_code != 200:
            return None
        return response.json()["csrf_token"]

    def mutate(self, client, method, path, csrf, **kwargs):
        return client.request(
            method,
            path,
            headers={"X-CSRF-Token": csrf},
            **kwargs,
        )

    def register(self, client, email, name="Student"):
        csrf = self.csrf(client)
        response = self.mutate(
            client,
            "POST",
            "/auth/register",
            csrf,
            json={
                "name": name,
                "email": email,
                "password": "a-valid-password-123",
            },
        )
        return response

    def test_registration_login_profile_duplicate_and_logout(self):
        self.assertEqual(self.client.get("/conversations").status_code, 401)
        csrf = self.csrf(self.client)
        invalid_registration = self.mutate(
            self.client,
            "POST",
            "/auth/register",
            csrf,
            json={
                "name": "Student",
                "email": "invalid-password@example.edu",
                "password": "hidden!",
            },
        )
        self.assertEqual(invalid_registration.status_code, 422)
        self.assertNotIn("hidden!", invalid_registration.text)

        created = self.register(self.client, "  STUDENT@EXAMPLE.EDU ")
        self.assertEqual(created.status_code, 201)
        self.assertNotIn("password_hash", created.json()["user"])
        cookie_headers = created.headers.get_list("set-cookie")
        self.assertTrue(
            any("eduguide_session=" in cookie and "HttpOnly" in cookie for cookie in cookie_headers)
        )

        duplicate = self.register(self.client, "student@example.edu")
        self.assertEqual(duplicate.status_code, 409)

        self.assertEqual(self.client.get("/auth/me").status_code, 200)
        self.assertEqual(
            self.client.get("/auth/me").json()["user"]["email"],
            "student@example.edu",
        )

        csrf = self.csrf(self.client)
        logout = self.mutate(self.client, "POST", "/auth/logout", csrf, json={})
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(self.client.get("/auth/me").status_code, 401)

        csrf = self.csrf(self.client)
        invalid_login = self.mutate(
            self.client,
            "POST",
            "/auth/login",
            csrf,
            json={"email": "student@example.edu", "password": "wrong-password"},
        )
        self.assertEqual(invalid_login.status_code, 401)
        self.assertEqual(
            invalid_login.json()["detail"],
            "Email or password is incorrect.",
        )

        csrf = self.csrf(self.client)
        login = self.mutate(
            self.client,
            "POST",
            "/auth/login",
            csrf,
            json={
                "email": "student@example.edu",
                "password": "a-valid-password-123",
            },
        )
        self.assertEqual(login.status_code, 200)
        self.assertEqual(self.client.get("/auth/me").status_code, 200)

        # Direct login without CSRF header (such as from Swagger UI or API client)
        swagger_client = TestClient(main.app)
        direct_login = swagger_client.post(
            "/auth/login",
            json={
                "email": "student@example.edu",
                "password": "a-valid-password-123",
            },
        )
        self.assertEqual(direct_login.status_code, 200)
        self.assertEqual(swagger_client.get("/auth/me").status_code, 200)

        # Authenticated state-changing endpoints strictly enforce CSRF
        no_csrf_mutation = swagger_client.post("/conversations", json={})
        self.assertEqual(no_csrf_mutation.status_code, 403)
        self.assertEqual(no_csrf_mutation.json()["detail"], "CSRF validation failed.")
        swagger_client.close()

    def test_cors_preflight_for_production_origin(self):
        response = self.client.options(
            "/auth/login",
            headers={
                "Origin": "https://eduguide-frontend.onrender.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-csrf-token",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get("access-control-allow-origin"),
            "https://eduguide-frontend.onrender.com",
        )
        self.assertEqual(
            response.headers.get("access-control-allow-credentials"),
            "true",
        )

    def test_chat_history_is_persistent_and_owned(self):
        self.assertEqual(self.register(self.client, "first@example.edu").status_code, 201)
        csrf = self.csrf(self.client)
        conversation = self.mutate(
            self.client,
            "POST",
            "/conversations",
            csrf,
            json={},
        ).json()

        with patch.object(main, "answer_question", return_value="A saved answer"):
            chat = self.mutate(
                self.client,
                "POST",
                "/chat",
                csrf,
                json={
                    "question": "Explain semantic search",
                    "conversation_id": conversation["id"],
                },
            )
        self.assertEqual(chat.status_code, 200)
        self.assertEqual(chat.json()["answer"], "A saved answer")
        self.assertEqual(
            len(self.client.get(f"/conversations/{conversation['id']}/messages").json()),
            2,
        )

        second_client = TestClient(main.app)
        self.assertEqual(
            self.register(second_client, "second@example.edu").status_code,
            201,
        )
        self.assertEqual(
            second_client.get(
                f"/conversations/{conversation['id']}/messages"
            ).status_code,
            404,
        )
        second_csrf = self.csrf(second_client)
        self.assertEqual(
            self.mutate(
                second_client,
                "PATCH",
                f"/conversations/{conversation['id']}",
                second_csrf,
                json={"title": "Stolen"},
            ).status_code,
            404,
        )
        self.assertEqual(
            self.mutate(
                second_client,
                "DELETE",
                f"/conversations/{conversation['id']}",
                second_csrf,
            ).status_code,
            404,
        )
        second_client.close()

        csrf = self.csrf(self.client)
        self.mutate(self.client, "POST", "/auth/logout", csrf, json={})
        csrf = self.csrf(self.client)
        self.mutate(
            self.client,
            "POST",
            "/auth/login",
            csrf,
            json={
                "email": "first@example.edu",
                "password": "a-valid-password-123",
            },
        )
        history = self.client.get("/conversations").json()
        self.assertEqual([item["id"] for item in history], [conversation["id"]])

    def test_pdf_upload_is_indexed_and_retrieval_is_conversation_scoped(self):
        user = self.register(self.client, "notes-owner@example.edu").json()["user"]
        csrf = self.csrf(self.client)
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            page.insert_text(
                (72, 72),
                "Volume in Big Data is the large amount of information "
                "collected, stored, and processed by a system.",
            )
            pdf_content = pdf.tobytes()

        with tempfile.TemporaryDirectory() as upload_directory:
            with patch.object(main, "UPLOAD_DIR", Path(upload_directory)):
                unsupported = self.mutate(
                    self.client,
                    "POST",
                    "/documents/upload",
                    csrf,
                    files={
                        "file": (
                            "slides.pptx",
                            b"not a supported document",
                            "application/octet-stream",
                        )
                    },
                )
                self.assertEqual(unsupported.status_code, 415)

                unrelated_pdf = self.mutate(
                    self.client,
                    "POST",
                    "/documents/upload",
                    csrf,
                    files={
                        "file": (
                            "damaged.pdf",
                            b"%PDF-not-a-real-pdf",
                            "application/pdf",
                        )
                    },
                )
                self.assertEqual(unrelated_pdf.status_code, 422)

                uploaded = self.mutate(
                    self.client,
                    "POST",
                    "/documents/upload",
                    csrf,
                    files={
                        "file": (
                            "Big Data.pdf",
                            pdf_content,
                            "application/pdf",
                        )
                    },
                )
                self.assertEqual(uploaded.status_code, 201, uploaded.text)
                document = uploaded.json()
                self.addCleanup(remove_material, document["id"])
                conversation_id = document["conversation_id"]
                self.assertEqual(document["status"], "indexed")
                self.assertTrue(conversation_id)
                self.assertIsNotNone(get_material(document["id"], user["id"]))

                stored_documents = self.client.get(
                    f"/conversations/{conversation_id}/documents"
                )
                self.assertEqual(stored_documents.status_code, 200)
                self.assertEqual(
                    [item["id"] for item in stored_documents.json()],
                    [document["id"]],
                )

                context, sources = retrieve_context_with_sources(
                    "What is Volume in Big Data?",
                    [document["id"]],
                    user["id"],
                )
                self.assertIn("large amount of information", context)
                self.assertEqual(sources[0]["page_number"], 1)
                self.assertEqual(sources[0]["filename"], "Big Data.pdf")
                unrelated_context, unrelated_sources = retrieve_context_with_sources(
                    "Explain quantum chromodynamics and gluons.",
                    [document["id"]],
                    user["id"],
                )
                self.assertEqual(unrelated_context, "")
                self.assertEqual(unrelated_sources, [])

                with patch.object(
                    main,
                    "answer_question",
                    return_value=("Volume is the amount of data.", sources),
                ) as answer_mock:
                    chat = self.mutate(
                        self.client,
                        "POST",
                        "/chat",
                        csrf,
                        json={
                            "question": "What is Volume in Big Data?",
                            "conversation_id": conversation_id,
                            "document_ids": [document["id"]],
                        },
                    )
                self.assertEqual(chat.status_code, 200, chat.text)
                self.assertEqual(chat.json()["sources"], sources)
                self.assertEqual(answer_mock.call_args.kwargs["owner_id"], user["id"])
                self.assertEqual(
                    answer_mock.call_args.args[1],
                    [document["id"]],
                )
                restored_messages = self.client.get(
                    f"/conversations/{conversation_id}/messages"
                ).json()
                self.assertEqual(restored_messages[-1]["sources"], sources)
                second_conversation = self.mutate(
                    self.client,
                    "POST",
                    "/conversations",
                    csrf,
                    json={},
                ).json()
                attached = self.mutate(
                    self.client,
                    "POST",
                    f"/conversations/{second_conversation['id']}/documents",
                    csrf,
                    json={"document_id": document["id"]},
                )
                self.assertEqual(attached.status_code, 200, attached.text)
                self.assertEqual(
                    len(
                        self.client.get(
                            f"/conversations/{second_conversation['id']}/documents"
                        ).json()
                    ),
                    1,
                )
                detached = self.mutate(
                    self.client,
                    "DELETE",
                    f"/conversations/{second_conversation['id']}/documents/{document['id']}",
                    csrf,
                )
                self.assertEqual(detached.status_code, 204)
                self.assertEqual(
                    self.client.get(
                        f"/conversations/{second_conversation['id']}/documents"
                    ).json(),
                    [],
                )

                second_client = TestClient(main.app)
                try:
                    self.assertEqual(
                        self.register(
                            second_client,
                            "notes-other@example.edu",
                        ).status_code,
                        201,
                    )
                    self.assertEqual(
                        second_client.get(
                            f"/conversations/{conversation_id}/documents"
                        ).status_code,
                        404,
                    )
                    self.assertEqual(
                        self.mutate(
                            second_client,
                            "POST",
                            f"/conversations/{conversation_id}/documents",
                            self.csrf(second_client),
                            json={"document_id": document["id"]},
                        ).status_code,
                        404,
                    )
                finally:
                    second_client.close()

    def test_llm_prompt_does_not_add_study_material_disclaimer(self):
        import llm_service

        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Volume is the amount of data.")
                )
            ]
        )
        with patch.object(
            llm_service.client.chat.completions,
            "create",
            return_value=response,
        ) as create:
            self.assertEqual(
                llm_service.ask_llm("What is Volume?", ""),
                "Volume is the amount of data.",
            )
        prompts = "\n".join(
            message["content"] for message in create.call_args.kwargs["messages"]
        ).lower()
        self.assertNotIn("supplements the available study material", prompts)
        self.assertIn("start with the answer", prompts)

    def test_failed_answer_can_be_retried_without_duplicate_question(self):
        self.assertEqual(self.register(self.client, "retry@example.edu").status_code, 201)
        csrf = self.csrf(self.client)

        with patch.object(
            main,
            "answer_question",
            side_effect=[RuntimeError("private provider detail"), "Recovered answer"],
        ):
            first = self.mutate(
                self.client,
                "POST",
                "/chat",
                csrf,
                json={"question": "Explain the retry behavior"},
            )
            self.assertEqual(first.status_code, 502)
            conversation_id = first.json()["detail"]["conversation_id"]
            second = self.mutate(
                self.client,
                "POST",
                "/chat",
                csrf,
                json={
                    "question": "Explain the retry behavior",
                    "conversation_id": conversation_id,
                },
            )

        self.assertEqual(second.status_code, 200)
        messages = self.client.get(
            f"/conversations/{conversation_id}/messages"
        ).json()
        self.assertEqual([message["role"] for message in messages], ["user", "assistant"])
        self.assertEqual(messages[0]["content"], "Explain the retry behavior")

    def test_database_outage_is_reported_without_exposing_connection_details(self):
        def unavailable_database():
            raise OperationalError(
                "connection",
                {},
                RuntimeError("private database connection details"),
            )
            yield

        main.app.dependency_overrides[get_db] = unavailable_database
        response = self.client.get("/health/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"],
            "The database is temporarily unavailable. Please try again.",
        )
        self.assertNotIn("private database connection details", response.text)

    def test_document_access_is_limited_to_its_owner(self):
        first_user = self.register(self.client, "document-owner@example.edu").json()["user"]
        document_id = "00000000-0000-4000-8000-000000000001"
        with self.session_factory() as db:
            db.add(
                Document(
                    id=document_id,
                    user_id=first_user["id"],
                    filename="private-notes.txt",
                    storage_path="unused-private-file.txt",
                    status="indexed",
                    file_size=128,
                )
            )
            db.commit()

        second_client = TestClient(main.app)
        self.assertEqual(
            self.register(second_client, "other-student@example.edu").status_code,
            201,
        )
        self.assertEqual(second_client.get("/documents").json()["documents"], [])
        csrf = self.csrf(second_client)
        self.assertEqual(
            self.mutate(
                second_client,
                "DELETE",
                f"/documents/{document_id}",
                csrf,
            ).status_code,
            404,
        )
        self.assertEqual(
            self.mutate(
                second_client,
                "POST",
                "/chat",
                csrf,
                json={
                    "question": "Read the other student's notes",
                    "document_ids": [document_id],
                },
            ).status_code,
            404,
        )
        second_client.close()


if __name__ == "__main__":
    unittest.main()
