# -*- coding: utf-8 -*-
"""
`_sin_cache` (app.py) -- páginas HTML siempre `no-store` (hallazgo 03,
auditoría 2026-09-15); `/static` con un `Cache-Control` positivo (reportado
en vivo 2026-09-19, escáner ZXing de guía) para que el navegador no tenga
que revalidar por red en cada carga de página.
"""


def test_pagina_html_sale_sin_cache(client):
    r = client.get("/ingresar")
    assert r.headers["cache-control"] == "no-store"


def test_asset_estatico_sale_con_cache_positivo(client):
    r = client.get("/static/vendor/zxing.min.js")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=86400"
