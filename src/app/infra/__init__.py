# -*- coding: utf-8 -*-
"""
Paquete aislado para código que habla con infraestructura EXTERNA al proceso
de la app (el servidor de despliegue, no un proveedor de notificación) --
ver `app/infra/deploy_ssh.py`. Mismo espíritu de aislamiento que `app/domain`/
`app/web` (ADR-0004): no importa el mundo legacy.
"""
