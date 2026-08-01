# -*- coding: utf-8 -*-
"""
Íconos de navegación — compartidos entre `base.html` (header/footer) y los
componentes de formulario del design system (`_inputs.html`, `_botones.html`).

Global de Jinja (ver `templating.py`), no una variable local de `base.html`:
los macros de `components/*.html` se importan con `{% from ... import %}` y
NO heredan el contexto de quien los llama salvo que se pase explícito -- un
global es la única forma limpia de que `_inputs.html` (u otro macro) pueda
usar un ícono por nombre sin que cada plantilla se lo tenga que pasar como
string literal repetido.

Ayuda/Whatsapp/Teléfono(footer) son los mismos íconos exactos de producción
(paqueteex.papyrus.com.co); la mayoría del resto usa el mismo estilo
Heroicons solid (viewBox 20x20, `fill="currentColor"`) que ya usa todo el
design system. Un subconjunto (`paquetes`, `entrar`/`persona`,
`telefono_campo`, `email`, `candado`, `casa`) es estilo Heroicons OUTLINE
(viewBox 24x24, `stroke="currentColor"`, `fill="none"`) -- Login y Paquetes
son los paths EXACTOS de producción (Tailwind, verificados contra su HTML
servido, 2026-08-01); Email/Candado/Casa no tienen referencia de producción
así que se diseñaron a mano (geometría simple, verificada visualmente antes
de commitear) siguiendo el mismo lenguaje visual outline.

`entrar` (ícono del botón de login público) y `persona` (ícono de campos de
nombre en formularios) son el MISMO path -- dos claves separadas por
claridad de uso, no porque el dibujo difiera.
"""

ICONOS_NAV = {
    "anunciar": '<path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm.75-11.25a.75.75 0 00-1.5 0v2.5h-2.5a.75.75 0 000 1.5h2.5v2.5a.75.75 0 001.5 0v-2.5h2.5a.75.75 0 000-1.5h-2.5v-2.5z" clip-rule="evenodd"/>',
    "buscar": '<path fill-rule="evenodd" d="M9 3.5a5.5 5.5 0 100 11 5.5 5.5 0 000-11zM2 9a7 7 0 1112.452 4.391l3.328 3.329a.75.75 0 11-1.06 1.06l-3.329-3.328A7 7 0 012 9z" clip-rule="evenodd"/>',
    "paquetes": '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4"/>',
    "clientes": '<path d="M7 8a3 3 0 100-6 3 3 0 000 6zM14.5 9a2.5 2.5 0 100-5 2.5 2.5 0 000 5zM1.615 16.428a1.224 1.224 0 01-.569-1.175 6.002 6.002 0 0111.908 0c.058.467-.172.92-.57 1.174A9.953 9.953 0 017 18a9.953 9.953 0 01-5.385-1.572zM14.5 16h-.106c.005-.11.008-.22.008-.331 0-1.153-.433-2.294-1.155-3.348A3.987 3.987 0 0119.5 15.02c.052.47-.202.902-.605 1.154A6.98 6.98 0 0114.5 16z"/>',
    "ayuda": '<path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zM8.94 6.94a.75.75 0 11-1.061-1.061 3 3 0 112.871 5.026v.345a.75.75 0 01-1.5 0v-.5c0-.72.57-1.172 1.081-1.287.87-.196 1.359-.986.99-1.723a1.5 1.5 0 00-2.38-.36zM10 15a1 1 0 100-2 1 1 0 000 2z" clip-rule="evenodd"/>',
    "entrar": '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"/>',
    "persona": '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"/>',
    "telefono_campo": '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 5a2 2 0 012-2h3.28a1 1 0 01.948.684l1.498 4.493a1 1 0 01-.502 1.21l-2.257 1.13a11.042 11.042 0 005.516 5.516l1.13-2.257a1 1 0 011.21-.502l4.493 1.498a1 1 0 01.684.949V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 6V5z"/>',
    "email": '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 5H21V19H3Z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 5L12 13L21 5"/>',
    "candado": '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 11V8a4 4 0 118 0v3"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 11H19V21H5Z"/>',
    "casa": '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 11L12 4L20 11"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 10V20H18V10"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 20V14H14V20"/>',
    "whatsapp": '<path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893A11.821 11.821 0 0020.885 3.488"/>',
    "telefono": '<path fill-rule="evenodd" d="M2 3.5A1.5 1.5 0 013.5 2h1.148a1.5 1.5 0 011.465 1.175l.716 3.223a1.5 1.5 0 01-1.052 1.767l-.933.267c-.41.117-.643.555-.48.95a11.542 11.542 0 006.254 6.254c.395.163.833-.07.95-.48l.267-.933a1.5 1.5 0 011.767-1.052l3.223.716A1.5 1.5 0 0118 15.352V16.5a1.5 1.5 0 01-1.5 1.5H15c-1.149 0-2.263-.15-3.326-.43A13.022 13.022 0 012.43 8.326 13.019 13.019 0 012 5V3.5z" clip-rule="evenodd"/>',
    "mis_datos": '<path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-5.5-2.5a2.5 2.5 0 11-5 0 2.5 2.5 0 015 0zM10 12a5.99 5.99 0 00-4.793 2.39A6.483 6.483 0 0010 16.5a6.483 6.483 0 004.793-2.11A5.99 5.99 0 0010 12z" clip-rule="evenodd"/>',
    "personal": '<path d="M4.5 2A1.5 1.5 0 003 3.5v13A1.5 1.5 0 004.5 18h11a1.5 1.5 0 001.5-1.5v-13A1.5 1.5 0 0015.5 2h-11zM8 5a2 2 0 100 4 2 2 0 000-4zM4.5 12.5a3.5 3.5 0 017 0 .5.5 0 01-.5.5h-6a.5.5 0 01-.5-.5zM13 6a1 1 0 100 2h2a1 1 0 100-2h-2zm-1 4a1 1 0 011-1h2a1 1 0 110 2h-2a1 1 0 01-1-1z"/>',
    "notificaciones": '<path d="M10 2a6 6 0 00-6 6c0 1.887-.454 3.665-1.257 5.234a.75.75 0 00.515 1.076 32.91 32.91 0 003.256.508 3.5 3.5 0 006.972 0 32.903 32.903 0 003.256-.508.75.75 0 00.515-1.076A11.448 11.448 0 0116 8a6 6 0 00-6-6zM8.05 14.943a33.54 33.54 0 003.9 0 2 2 0 01-3.9 0z"/>',
    "salir": '<path fill-rule="evenodd" d="M3 4.25A2.25 2.25 0 015.25 2h5.5A2.25 2.25 0 0113 4.25v2a.75.75 0 01-1.5 0v-2a.75.75 0 00-.75-.75h-5.5a.75.75 0 00-.75.75v11.5c0 .414.336.75.75.75h5.5a.75.75 0 00.75-.75v-2a.75.75 0 011.5 0v2A2.25 2.25 0 0110.75 18h-5.5A2.25 2.25 0 013 15.75V4.25z" clip-rule="evenodd"/><path fill-rule="evenodd" d="M6 10a.75.75 0 01.75-.75h9.546l-1.048-.943a.75.75 0 111.004-1.114l2.5 2.25a.75.75 0 010 1.114l-2.5 2.25a.75.75 0 11-1.004-1.114l1.048-.943H6.75A.75.75 0 016 10z" clip-rule="evenodd"/>',
}
