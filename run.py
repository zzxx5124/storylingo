import uvicorn

from backend import settings

if __name__ == "__main__":
    host = "0.0.0.0" if settings.APP_ENV == "production" else "127.0.0.1"
    print(f"小說朗讀啟動： http://{host}:{settings.PORT}")
    uvicorn.run("backend.main:app", host=host, port=settings.PORT, access_log=False)
