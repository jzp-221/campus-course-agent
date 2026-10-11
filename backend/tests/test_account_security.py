"""真实 HTTP/JWT 与 MCP 鉴权，数据库仅使用内存 SQLite。"""

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

import httpx
import jwt
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tests import test_agent  # Initialise offline settings before app imports.
from app.api.auth import router as auth_router
from app.api.user import router as user_router
from app.common.exceptions import (BusinessException, business_exception_handler,
    global_excpetion_hadler, http_excpetion_hadler, validation_excpetion_hadler)
from app.config import settings
from app.database import Base, get_db
from app.dependencies import mcp_auth
from app.models.user import User
from app.utils.jwt import create_access_token, decode_access_token
from app.utils.password import hash_password, verify_password


class AccountSecurityTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.initial_hash = hash_password("123")

    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, autoflush=False)
        with self.sessions() as db:
            users = [User(username=name, name=name, password=self.initial_hash,
                          role="admin" if name == "admin" else "student")
                     for name in ("aaa", "bbb", "admin")]
            db.add_all(users)
            db.commit()
            self.ids = {user.username: user.id for user in users}
        self.tokens = {name: create_access_token(identifier, self.initial_hash)
                       for name, identifier in self.ids.items()}
        self.app = FastAPI()
        for router in (auth_router, user_router):
            self.app.include_router(router, prefix="/api")
        for exception, handler in ((BusinessException, business_exception_handler),
                                  (HTTPException, http_excpetion_hadler),
                                  (RequestValidationError, validation_excpetion_hadler),
                                  (Exception, global_excpetion_hadler)):
            self.app.add_exception_handler(exception, handler)

        def dependency():
            with self.sessions() as db:
                yield db

        self.app.dependency_overrides[get_db] = dependency
        self.mcp_session_patch = patch.object(mcp_auth, "SessionLocal", self.sessions)
        self.mcp_session_patch.start()
        self.addCleanup(self.mcp_session_patch.stop)

    async def call(self, method, path, *, payload=None, token=None):
        headers = {"Authorization": "Bearer " + token} if token else {}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(self.app, raise_app_exceptions=False),
                                     base_url="http://offline") as client:
            return await client.request(method, path, json=payload, headers=headers)

    async def login(self, username="aaa", password="123"):
        response = await self.call("POST", "/api/auth/login", payload={"username": username, "password": password})
        self.assertEqual(response.json()["code"], 200, response.text)
        return response.json()["data"]["token"]

    def user_count(self):
        with self.sessions() as db:
            return db.query(User).count()

    async def assert_rejected_by_http_and_mcp(self, token):
        response = await self.call("GET", "/api/user/me", token=token)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], 401)
        with self.assertRaises(BusinessException) as error:
            mcp_auth.user_id_from_authorization("Bearer " + token)
        self.assertEqual(error.exception.code, 401)

    async def test_registration_blank_account_and_password_are_business_400_without_insert(self):
        for fields in ({"username": "", "password": "123"}, {"username": " \t", "password": "123"},
                       {"username": "new-user", "password": ""}, {"username": "new-user", "password": " \n"},
                       {"username": None, "password": "123"}, {"username": 123, "password": "123"}):
            with self.subTest(fields=fields):
                result = await self.call("POST", "/api/auth/register", payload=fields)
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json()["code"], 400)
        self.assertEqual(self.user_count(), 3)

    async def test_registration_username_is_trimmed_and_default_name_remains_usable(self):
        result = await self.call("POST", "/api/auth/register", payload={"username": "  new-user  ", "password": "123"})
        self.assertEqual(result.json()["code"], 200)
        token = await self.login("new-user")
        result = await self.call("GET", "/api/user/me", token=token)
        self.assertEqual(result.json()["data"]["name"], "new-user")

    async def test_username_and_name_lengths_fail_before_database(self):
        for fields in ({"username": "x" * 51, "password": "123"},
                       {"username": "new-user", "password": "123", "name": "名" * 51}):
            response = await self.call("POST", "/api/auth/register", payload=fields)
            self.assertEqual(response.json()["code"], 400)
        self.assertEqual(self.user_count(), 3)

    async def test_ascii_and_chinese_oversized_passwords_are_400_on_register_and_login(self):
        for password in ("a" * 73, "密" * 25):
            for path in ("/api/auth/register", "/api/auth/login"):
                result = await self.call("POST", path, payload={"username": "aaa", "password": password})
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json()["code"], 400)
                self.assertIn("72字节", result.json()["message"])
        self.assertEqual(self.user_count(), 3)

    async def test_admin_user_creation_uses_same_validation_and_keeps_default_password(self):
        for fields in ({"username": " "}, {"username": "new-user", "password": "a" * 73}):
            result = await self.call("POST", "/api/user", payload=fields, token=self.tokens["admin"])
            self.assertEqual(result.json()["code"], 400)
        result = await self.call("POST", "/api/user", payload={"username": "new-user"}, token=self.tokens["admin"])
        self.assertEqual(result.json()["code"], 200)
        self.assertEqual(result.json()["data"]["name"], "new-user")
        await self.login("new-user")

    async def test_invalid_new_password_keeps_hash_and_current_token(self):
        for value in ("", "  ", "a" * 73, "密" * 25):
            response = await self.call("PUT", "/api/user/password", token=self.tokens["aaa"],
                payload={"old_password": "123", "new_password": value})
            self.assertEqual(response.json()["code"], 400)
        response = await self.call("GET", "/api/user/me", token=self.tokens["aaa"])
        self.assertEqual(response.json()["code"], 200)
        with self.sessions() as db:
            self.assertEqual(db.get(User, self.ids["aaa"]).password, self.initial_hash)

    async def test_password_change_revokes_all_old_http_and_mcp_tokens_and_new_login_works(self):
        old_tokens = [self.tokens["aaa"], await self.login()]
        response = await self.call("PUT", "/api/user/password", token=old_tokens[0],
                                  payload={"old_password": "123", "new_password": "456"})
        self.assertEqual(response.json()["code"], 200)
        for token in old_tokens:
            await self.assert_rejected_by_http_and_mcp(token)
        new_token = await self.login(password="456")
        response = await self.call("GET", "/api/user/me", token=new_token)
        self.assertEqual(response.json()["code"], 200)
        self.assertEqual(mcp_auth.user_id_from_authorization("Bearer " + new_token), self.ids["aaa"])
        response = await self.call("POST", "/api/auth/login", payload={"username": "aaa", "password": "123"})
        self.assertNotEqual(response.json()["code"], 200)

    async def test_password_change_does_not_revoke_another_students_token(self):
        response = await self.call("PUT", "/api/user/password", token=self.tokens["aaa"],
                                  payload={"old_password": "123", "new_password": "456"})
        self.assertEqual(response.json()["code"], 200)
        response = await self.call("GET", "/api/user/me", token=self.tokens["bbb"])
        self.assertEqual(response.json()["data"]["id"], self.ids["bbb"])
        self.assertEqual(mcp_auth.user_id_from_authorization("Bearer " + self.tokens["bbb"]), self.ids["bbb"])

    async def test_profile_edit_does_not_revoke_current_login(self):
        response = await self.call("PUT", "/api/user/me", token=self.tokens["aaa"], payload={"name": "新姓名"})
        self.assertEqual(response.json()["code"], 200)
        response = await self.call("GET", "/api/user/me", token=self.tokens["aaa"])
        self.assertEqual(response.json()["data"]["name"], "新姓名")
        self.assertEqual(mcp_auth.user_id_from_authorization("Bearer " + self.tokens["aaa"]), self.ids["aaa"])

    async def test_legacy_token_without_password_version_requires_relogin_on_both_paths(self):
        token = jwt.encode({"user_id": self.ids["aaa"], "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                           settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
        await self.assert_rejected_by_http_and_mcp(token)

    async def test_wrong_or_malformed_password_version_returns_401_not_server_error(self):
        payload = decode_access_token(self.tokens["aaa"])
        for value in ("0" * 64, "密" * 64, None, 42, {}, ""):
            payload["pwdv"] = value
            token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
            await self.assert_rejected_by_http_and_mcp(token)

    async def test_login_jwt_contains_no_password_or_password_hash(self):
        token = await self.login()
        payload = decode_access_token(token)
        self.assertEqual(set(payload), {"user_id", "exp", "pwdv"})
        self.assertNotIn(self.initial_hash, str(payload))
        self.assertNotEqual(payload["pwdv"], self.initial_hash)

    def test_bcrypt_72_byte_boundaries_and_literal_spaces_are_preserved(self):
        for password in ("a" * 72, "密" * 24, " 123 "):
            hashed = hash_password(password)
            self.assertTrue(verify_password(password, hashed))
        self.assertFalse(verify_password("123", hash_password(" 123 ")))
        for value in ("a" * 73, "密" * 25, "", " ", "\ud800"):
            with self.assertRaises(BusinessException) as error:
                hash_password(value)
            self.assertEqual(error.exception.code, 400)
        self.assertFalse(verify_password("123", "not-a-bcrypt-hash"))
