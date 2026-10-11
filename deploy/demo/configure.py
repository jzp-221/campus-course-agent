"""Generate NEW isolated demo credentials without touching backend/.env."""

import argparse
import os
from pathlib import Path
import secrets
from urllib.parse import urlparse


def generate_config(site):
    if not site or any(character.isspace() for character in site):
        raise ValueError("Use a bare domain (HTTPS), or http://127.0.0.1:18080 for a local test")
    parsed = urlparse(site if "://" in site else "https://" + site)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
            or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            or any(character in site for character in "$#{}'\"\\")):
        raise ValueError("Invalid demo site address")
    password = secrets.token_hex(24)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    values = {
        "DEMO_SITE_ADDRESS": site.rstrip("/"), "DEMO_CORS_ORIGINS": origin,
        "DEMO_DB_PASSWORD": password, "DEMO_DB_ROOT_PASSWORD": secrets.token_hex(24),
        "DATABASE_URL": f"mysql+pymysql://campus_demo:{password}@db:3306/campus_demo",
        "JWT_SECRET_KEY": secrets.token_hex(32),
        "DEMO_ADMIN_PASSWORD": secrets.token_urlsafe(24),
        "DEMO_STUDENT_PASSWORD": secrets.token_urlsafe(16),
        "LLM_API_KEY": "", "LLM_BASE_URL": "https://api.deepseek.com", "LLM_MODEL": "deepseek-chat",
        "LLM_TIMEOUT_SECONDS": "30", "AGENT_MAX_ITERATIONS": "4",
        "EMBEDDING_API_KEY": "", "EMBEDDING_BASE_URL": "https://api.siliconflow.cn/v1",
        "EMBEDDING_MODEL": "BAAI/bge-m3", "DEMO_INDEX_DOCUMENTS": "false",
        "DEMO_AI_PER_MINUTE": "3", "DEMO_AI_PER_USER_DAY": "20", "DEMO_AI_GLOBAL_DAY": "60",
        "DEMO_AI_CONCURRENCY": "2", "DEMO_AI_TIMEOUT_SECONDS": "90", "DEMO_MAX_OUTPUT_TOKENS": "700",
    }
    return "# Independent public-demo configuration. Never commit this file.\n" + "".join(
        f"{key}={value}\n" for key, value in values.items())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", required=True, help="Public domain, e.g. demo.example.com")
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("demo.env"))
    args = parser.parse_args()
    try:
        config = generate_config(args.site)
        if args.output.name != "demo.env":
            raise ValueError("Output must be named demo.env; original .env files are never overwritten")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as file:
            file.write(config)
    except (ValueError, OSError) as error:
        parser.exit(1, f"Configuration not written: {error}\n")
    print(f"已创建独立配置：{args.output}（权限 600）；没有读取或修改 backend/.env。")
    print("请在服务器上自行填写两个演示专用 API Key，并设 DEMO_INDEX_DOCUMENTS=true。")
    print("演示学生 demo_aaa / demo_bbb 共用文件内 DEMO_STUDENT_PASSWORD；demo_admin 密码请保密。")


if __name__ == "__main__":
    main()
