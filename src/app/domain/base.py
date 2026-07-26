# -*- coding: utf-8 -*-
"""
Metadata dedicada del esquema nuevo (rebuild PaqueteXv.2).

Es una `Base` declarativa propia, independiente de `app.models.base.Base` y de
`app.database.Base`. La migración baseline de Alembic apunta SOLO a esta
metadata, de modo que `alembic upgrade head` construya únicamente las tablas
del modelo nuevo.
"""

from sqlalchemy.orm import declarative_base

Base = declarative_base()
