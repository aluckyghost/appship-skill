# AppShip P3 端到端测试样本：FastAPI（无 Dockerfile，验证自动生成）
from fastapi import FastAPI

app = FastAPI(title="AppShip Demo API")


@app.get("/")
def root():
    return {"service": "appship-demo", "status": "ok"}


@app.get("/health")
def health():
    return {"health": True}
