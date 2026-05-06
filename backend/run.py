"""
ClearWaterMark Backend - FastAPI 服务入口
"""
import argparse
import uvicorn


def main():
    parser = argparse.ArgumentParser(description="ClearWaterMark Backend Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    parser.add_argument("--port", type=int, default=8787, help="Port to bind")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    args = parser.parse_args()

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
