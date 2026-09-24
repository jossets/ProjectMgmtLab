import os

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.auth import Identity, get_identity
from app.config import SECRET_KEY, SESSION_HTTPS_ONLY
from app.courses import COURSES_DIR
from app.db import Base, engine, get_db, run_migrations
from app.models import SessionMembership
from app.routers import admin as admin_router
from app.routers import auth as auth_router
from app.routers import cours as cours_router
from app.routers import gantt as gantt_router
from app.routers import kanban as kanban_router
from app.routers import presence as presence_router
from app.routers import sessions as sessions_router
from app.routers import whiteboard as whiteboard_router

Base.metadata.create_all(bind=engine)
run_migrations(engine)

app = FastAPI(title="ProjectMgr")
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    same_site="lax",
    https_only=SESSION_HTTPS_ONLY,
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# only the images are served this way — never the .md files themselves,
# which would leak QCM answer keys and bypass the session-membership check
# that gates course *content* (see app/routers/cours.py)
COURSES_IMG_DIR = os.path.join(COURSES_DIR, "img")
os.makedirs(COURSES_IMG_DIR, exist_ok=True)
app.mount("/cours-images", StaticFiles(directory=COURSES_IMG_DIR), name="cours-images")

app.include_router(admin_router.router)
app.include_router(auth_router.router)
app.include_router(cours_router.router)
app.include_router(gantt_router.router)
app.include_router(kanban_router.router)
app.include_router(presence_router.router)
app.include_router(sessions_router.router)
app.include_router(whiteboard_router.router)


@app.get("/")
def home(request: Request, identity: Identity = Depends(get_identity), db: Session = Depends(get_db)):
    sessions = []
    if identity.user is not None and identity.user.role == "student":
        memberships = db.query(SessionMembership).filter(SessionMembership.user_id == identity.user.id).all()
        sessions = [m.session for m in memberships]
    return templates.TemplateResponse(request, "home.html", {"identity": identity, "sessions": sessions})


@app.get("/favicon.ico")
def favicon():
    # browsers probe this exact path directly regardless of the <link
    # rel="icon"> in base.html (e.g. on first load, before any page sets
    # it) — redirect to the real, already-linked icon rather than 404ing
    return RedirectResponse(url="/static/favicon.svg")
