"""Helpers for tests that drive the merged Community edit page (PR 2a).

The old `/admin/tools/communities/{id}/profile` page is gone; the profile now
saves through `/tools/communities/{slug}/edit`, which also needs the listing's
required name and URL. These helpers keep the older tests' shape (post a dict of
profile fields for a community id) while hitting the real route.
"""
import os

from linklib.db import Library


def community_slug(cid: int) -> str:
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        return lib.get_community(cid)["slug"]
    finally:
        lib.close()


def edit_url(cid: int) -> str:
    return f"/tools/communities/{community_slug(cid)}/edit"


def post_profile(client, cid: int, data=None, follow_redirects=False, **kw):
    lib = Library(os.environ["LINKLIB_DB"])
    try:
        c = lib.get_community(cid)
    finally:
        lib.close()
    body = {"name": c["name"], "url": c["url"], "cost_band": c["cost_band"]}
    body.update(data or {})
    return client.post(f"/tools/communities/{c['slug']}/edit", data=body,
                       follow_redirects=follow_redirects, **kw)


def get_profile(client, cid: int):
    return client.get(edit_url(cid))
