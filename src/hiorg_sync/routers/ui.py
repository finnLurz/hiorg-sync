# src/hiorg_sync/routers/ui.py
from __future__ import annotations

import json
import secrets

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse

from ..core.settings import get_ov_list, require_ov
from ..core.security import (
    UI_PASSWORD,
    UI_SESSION_TTL_HOURS,
    ui_make_session,
    require_ui_login,
)

from ..services.notify import send_mail
from ..services.config_store import (
    read_json,
    write_json_atomic,
    OU_MAP_PATH,
    EMAIL_PATH,
    CONFIG_PATH,
)

router = APIRouter()


def _templates(request: Request):
    return request.app.state.templates


# -----------------------------
# Login
# -----------------------------
@router.get("/ui/login")
def ui_login_get(request: Request, next: str = "/ui/"):
    return _templates(request).TemplateResponse(
        "login.html",
        {"request": request, "next": next, "ttl_hours": UI_SESSION_TTL_HOURS},
    )


@router.post("/ui/login")
async def ui_login_post(request: Request):
    form = await request.form()
    pw = str(form.get("password", "") or "")
    nxt = str(form.get("next", "") or "/ui/")

    if UI_PASSWORD and not secrets.compare_digest(pw, UI_PASSWORD):
        raise HTTPException(401, "Invalid password")

    token = ui_make_session()
    resp = RedirectResponse(url=nxt, status_code=302)
    resp.set_cookie(
        "ui_session",
        token,
        httponly=True,
        secure=False,  # hinter HTTPS Proxy -> True
        samesite="lax",
        max_age=UI_SESSION_TTL_HOURS * 3600,
    )
    return resp


# -----------------------------
# Dashboard
# -----------------------------
@router.get("/ui/")
def ui_dashboard(request: Request):
    try:
        require_ui_login(request)
    except HTTPException:
        return RedirectResponse("/ui/login?next=/ui/", status_code=302)

    current_ov = request.cookies.get("ui_ov", "") or ""
    return _templates(request).TemplateResponse(
        "dashboard.html",
        {"request": request, "current_ov": current_ov, "ovs": get_ov_list()},
    )


# -----------------------------
# OV selection
# -----------------------------
@router.get("/ui/ov")
def ui_ov_get(request: Request, next: str = "/ui/groupmap"):
    try:
        require_ui_login(request)
    except HTTPException:
        return RedirectResponse("/ui/login?next=/ui/ov", status_code=302)

    current = request.cookies.get("ui_ov", "") or ""
    ovs = get_ov_list()
    return _templates(request).TemplateResponse(
        "ov.html",
        {"request": request, "ovs": ovs, "current": current, "next": next},
    )


@router.post("/ui/ov")
async def ui_ov_post(request: Request):
    try:
        require_ui_login(request)
    except HTTPException:
        return RedirectResponse("/ui/login?next=/ui/ov", status_code=302)

    form = await request.form()
    ov = str(form.get("ov", "") or "").strip()
    nxt = str(form.get("next", "") or "/ui/groupmap").strip() or "/ui/groupmap"

    if ov not in get_ov_list():
        raise HTTPException(400, "Invalid ov")

    # Wenn next /ui/groupmap ist, OV als query mitgeben (sauberer Flow)
    if nxt.startswith("/ui/groupmap") and "ov=" not in nxt:
        sep = "&" if "?" in nxt else "?"
        nxt = f"{nxt}{sep}ov={ov}"

    resp = RedirectResponse(url=nxt, status_code=302)
    resp.set_cookie(
        "ui_ov",
        ov,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=UI_SESSION_TTL_HOURS * 3600,
    )
    return resp


# -----------------------------
# Groupmap UI page
# -----------------------------
@router.get("/ui/groupmap")
def ui_groupmap(request: Request, ov: str | None = None):
    try:
        require_ui_login(request)
    except HTTPException:
        return RedirectResponse("/ui/login?next=/ui/ov", status_code=302)

    if not ov:
        ov = request.cookies.get("ui_ov", "")
    if not ov:
        return RedirectResponse("/ui/ov", status_code=302)

    require_ov(ov)

    return _templates(request).TemplateResponse(
        "groupmap.html",
        {"request": request, "ov": ov},
    )


# -----------------------------
# Settings: OU Map (OV -> User OU)
# -----------------------------
@router.get("/ui/settings/ou-map")
def ui_settings_ou_map_get(request: Request):
    try:
        require_ui_login(request)
    except HTTPException:
        return RedirectResponse("/ui/login?next=/ui/settings/ou-map", status_code=302)

    ovs = get_ov_list()

    raw = read_json(OU_MAP_PATH, default={})
    if not isinstance(raw, dict):
        raw = {}
    ou_map = {str(k).lower(): str(v) for k, v in raw.items() if str(k).strip() and str(v).strip()}

    return _templates(request).TemplateResponse(
        "settings_ou_map.html",
        {"request": request, "ovs": ovs, "ou_map": ou_map, "saved": False, "error": ""},
    )


@router.post("/ui/settings/ou-map")
async def ui_settings_ou_map_post(request: Request):
    try:
        require_ui_login(request)
    except HTTPException:
        return RedirectResponse("/ui/login?next=/ui/settings/ou-map", status_code=302)

    form = await request.form()
    ovs = get_ov_list()

    out: dict[str, str] = {}
    for ov in ovs:
        v = str(form.get(f"ou_{ov}", "") or "").strip()
        if v:
            out[str(ov).lower()] = v

    write_json_atomic(OU_MAP_PATH, out)

    return _templates(request).TemplateResponse(
        "settings_ou_map.html",
        {"request": request, "ovs": ovs, "ou_map": out, "saved": True, "error": ""},
    )


# -----------------------------
# Settings: Email (SMTP)
# -----------------------------
@router.get("/ui/settings/email")
def ui_settings_email_get(request: Request):
    try:
        require_ui_login(request)
    except HTTPException:
        return RedirectResponse("/ui/login?next=/ui/settings/email", status_code=302)

    cfg = read_json(EMAIL_PATH, default={})
    if not isinstance(cfg, dict):
        cfg = {}

    # niemals Passwort anzeigen
    view = dict(cfg)
    if "SMTP_PASS" in view:
        view["SMTP_PASS"] = ""

    return _templates(request).TemplateResponse(
        "settings_email.html",
        {
            "request": request,
            "cfg": view,
            "saved": False,
            "tested": False,
            "test_ok": False,
            "test_error": "",
            "error": "",
        },
    )


@router.post("/ui/settings/email")
async def ui_settings_email_post(request: Request):
    try:
        require_ui_login(request)
    except HTTPException:
        return RedirectResponse("/ui/login?next=/ui/settings/email", status_code=302)

    form = await request.form()
    action = str(form.get("action", "save") or "save").strip().lower()

    old_cfg = read_json(EMAIL_PATH, default={})
    if not isinstance(old_cfg, dict):
        old_cfg = {}

    def _b(name: str, default: bool = False) -> bool:
        v = str(form.get(name, "") or "").strip().lower()
        if not v:
            return default
        return v in ("1", "true", "yes", "on")

    host = str(form.get("SMTP_HOST", "") or "").strip()
    port_s = str(form.get("SMTP_PORT", "") or "").strip()
    user = str(form.get("SMTP_USER", "") or "").strip()
    pw = str(form.get("SMTP_PASS", "") or "").strip()
    starttls = _b("SMTP_STARTTLS", True)
    use_ssl = _b("SMTP_SSL", False)
    notify_from = str(form.get("NOTIFY_FROM", "") or "").strip()

    try:
        port = int(port_s or "587")
    except Exception:
        port = 587

    new_cfg = {
        "SMTP_HOST": host,
        "SMTP_PORT": port,
        "SMTP_USER": user,
        "SMTP_STARTTLS": bool(starttls),
        "SMTP_SSL": bool(use_ssl),
        "NOTIFY_FROM": notify_from,
    }

    # Passwort nur überschreiben wenn eingegeben, sonst altes behalten
    if pw:
        new_cfg["SMTP_PASS"] = pw
    else:
        old_pw = str(old_cfg.get("SMTP_PASS", "") or "").strip()
        if old_pw:
            new_cfg["SMTP_PASS"] = old_pw

    write_json_atomic(EMAIL_PATH, new_cfg)

    # view ohne PW
    view = dict(new_cfg)
    view["SMTP_PASS"] = ""

    tested = False
    test_ok = False
    test_error = ""

    if action == "test":
        tested = True
        test_to = str(form.get("TEST_TO", "") or "").strip()
        if not test_to:
            test_ok = False
            test_error = "Bitte TEST_TO angeben (Empfaengeradresse)."
        else:
            ok, err = send_mail(
                test_to,
                "[HiOrg-Sync] Testmail",
                "Das ist eine Testmail aus HiOrg-Sync.\n\nWenn du das liest: SMTP Settings sind OK.",
            )
            test_ok = bool(ok)
            test_error = err or ""

    return _templates(request).TemplateResponse(
        "settings_email.html",
        {
            "request": request,
            "cfg": view,
            "saved": True,
            "tested": tested,
            "test_ok": test_ok,
            "test_error": test_error,
            "error": "",
        },
    )

# -----------------------------
# Settings: LDAP BaseDN (zentral, nach Standort)
# - config.json: base_dn_by_location
# - UI pflegt Zeilen, POST sendet hidden JSON
# -----------------------------
@router.get("/ui/settings/ldap")
def ui_settings_ldap_get(request: Request):
    try:
        require_ui_login(request)
    except HTTPException:
        return RedirectResponse("/ui/login?next=/ui/settings/ldap", status_code=302)

    cfg = read_json(CONFIG_PATH, default={})
    if not isinstance(cfg, dict):
        cfg = {}

    by_loc = cfg.get("base_dn_by_location")
    if not isinstance(by_loc, dict):
        by_loc = {}

    # normalize keys
    by_loc_norm: dict[str, str] = {}
    for k, v in by_loc.items():
        kk = str(k).strip().lower()
        vv = str(v).strip()
        if kk and vv:
            by_loc_norm[kk] = vv

    view_cfg = {"base_dn_by_location": by_loc_norm}

    return _templates(request).TemplateResponse(
        "settings_ldap.html",
        {"request": request, "cfg": view_cfg, "saved": False, "error": "", "cfg_path": str(CONFIG_PATH)},
    )


@router.post("/ui/settings/ldap")
async def ui_settings_ldap_post(request: Request):
    try:
        require_ui_login(request)
    except HTTPException:
        return RedirectResponse("/ui/login?next=/ui/settings/ldap", status_code=302)

    form = await request.form()
    raw_json = str(form.get("base_dn_by_location_json", "") or "").strip()

    by_loc_norm: dict[str, str] = {}
    if raw_json:
        try:
            parsed = json.loads(raw_json)
        except Exception:
            return _templates(request).TemplateResponse(
                "settings_ldap.html",
                {
                    "request": request,
                    "cfg": {"base_dn_by_location": {}},
                    "saved": False,
                    "error": "Ungültige Eingabe (konnte Daten nicht lesen).",
                    "cfg_path": str(CONFIG_PATH),
                },
            )

        if not isinstance(parsed, dict):
            return _templates(request).TemplateResponse(
                "settings_ldap.html",
                {
                    "request": request,
                    "cfg": {"base_dn_by_location": {}},
                    "saved": False,
                    "error": "Ungültige Eingabe (Format).",
                    "cfg_path": str(CONFIG_PATH),
                },
            )

        for k, v in parsed.items():
            kk = str(k).strip().lower()
            vv = str(v).strip()
            if kk and vv:
                by_loc_norm[kk] = vv

    out_cfg = {"base_dn_by_location": by_loc_norm}
    write_json_atomic(CONFIG_PATH, out_cfg)

    return _templates(request).TemplateResponse(
        "settings_ldap.html",
        {"request": request, "cfg": out_cfg, "saved": True, "error": "", "cfg_path": str(CONFIG_PATH)},
    )

