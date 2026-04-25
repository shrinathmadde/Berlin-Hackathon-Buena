"""Generic CRUD route factory — keeps boilerplate to a minimum.

For every SQLModel table we expose:
    GET    /{prefix}            list with limit/offset
    GET    /{prefix}/{id}       fetch one
    POST   /{prefix}            create
    PATCH  /{prefix}/{id}       partial update (the surgical-update path)
    DELETE /{prefix}/{id}       delete
"""
from typing import Any, Type

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, SQLModel, select

from app.database import get_session


def make_router(model: Type[SQLModel], prefix: str, pk_name: str, tag: str) -> APIRouter:
    router = APIRouter(prefix=f"/{prefix}", tags=[tag])

    @router.get("", response_model=list[model])
    def list_items(
        limit: int = Query(100, le=1000),
        offset: int = 0,
        session: Session = Depends(get_session),
    ):
        return session.exec(select(model).offset(offset).limit(limit)).all()

    @router.get("/{item_id}", response_model=model)
    def get_item(item_id: str, session: Session = Depends(get_session)):
        obj = session.get(model, item_id)
        if obj is None:
            raise HTTPException(404, f"{tag} {item_id} not found")
        return obj

    @router.post("", response_model=model, status_code=201)
    def create_item(payload: dict[str, Any], session: Session = Depends(get_session)):
        # SQLModel skips field coercion in __init__ for table=True models
        # (e.g. ISO strings -> datetime), so use model_validate for proper coercion.
        obj = model.model_validate(payload)
        existing = session.get(model, getattr(obj, pk_name))
        if existing is not None:
            raise HTTPException(409, f"{tag} {getattr(obj, pk_name)} already exists")
        session.add(obj)
        session.commit()
        session.refresh(obj)
        return obj

    @router.patch("/{item_id}", response_model=model)
    def patch_item(
        item_id: str,
        patch: dict[str, Any],
        session: Session = Depends(get_session),
    ):
        obj = session.get(model, item_id)
        if obj is None:
            raise HTTPException(404, f"{tag} {item_id} not found")
        # Run patch values through pydantic validation so types coerce correctly.
        clean = {k: v for k, v in patch.items() if hasattr(obj, k) and k != pk_name}
        if clean:
            validated = model.model_validate({**obj.model_dump(), **clean})
            for key in clean:
                setattr(obj, key, getattr(validated, key))
        session.add(obj)
        session.commit()
        session.refresh(obj)
        return obj

    @router.delete("/{item_id}", status_code=204)
    def delete_item(item_id: str, session: Session = Depends(get_session)):
        obj = session.get(model, item_id)
        if obj is None:
            raise HTTPException(404, f"{tag} {item_id} not found")
        session.delete(obj)
        session.commit()
        return None

    return router
