from urllib.parse import urljoin, urlparse, urlunparse, urlencode, parse_qsl, quote_plus, quote
import re
from typing import Mapping, Optional

def _slugify_for_path(s: str) -> str:
    s = re.sub(r'[^A-Za-z0-9]+', '-', s).strip('-').lower()
    return quote(s)

def build_search_url(
    base_url: str,
    url_prefix: Optional[str],
    url_suffix: Optional[str],
    params: Optional[Mapping[str, str]] = None,
    keyword: Optional[str] = None,
    location: Optional[str] = None,
) -> str:
    params  = dict(params or {})
    prefix  = (url_prefix or "").strip()
    suffix  = (url_suffix or "").strip()

    # Detect placeholders in any PATH (affects ?q/&l fallback)
    suffix_path_part = (suffix.split('?', 1)[0] if suffix else "")
    had_kw_placeholder  = ("{keyword}"  in prefix) or ("{keyword}"  in suffix_path_part)
    had_loc_placeholder = ("{location}" in prefix) or ("{location}" in suffix_path_part)

    # 1) base + prefix
    if prefix.startswith(("http://", "https://")):
        base = prefix
    elif prefix:
        base = urljoin(base_url.rstrip("/") + "/", prefix.lstrip("/"))
    else:
        base = base_url

    # 2) parse
    p = urlparse(base)
    path  = p.path
    query = dict(parse_qsl(p.query, keep_blank_values=True))

    # 3) substitute placeholders in PREFIX path (slug in path)
    if keyword  is not None and "{keyword}"  in path: path = path.replace("{keyword}",  _slugify_for_path(keyword))
    if location is not None and "{location}" in path: path = path.replace("{location}", _slugify_for_path(location))

    # 4) fallbacks only if no placeholder in PATH
    if keyword  is not None and not had_kw_placeholder  and "q" not in query and "q" not in params: query["q"] = keyword
    if location is not None and not had_loc_placeholder and "l" not in query and "l" not in params: query["l"] = location

    # 5) merge caller params
    query.update({k: v for k, v in params.items() if v is not None})

    # 6) apply SUFFIX (may be path + query), with placeholder support
    if suffix:
        suf = suffix.lstrip("?&")
        if "?" in suf:
            suf_path, suf_query = suf.split("?", 1)
        else:
            suf_path, suf_query = (suf, "") if ("/" in suf and "=" not in suf) else ("", suf)

        if suf_path:
            if keyword  is not None and "{keyword}"  in suf_path: suf_path = suf_path.replace("{keyword}",  _slugify_for_path(keyword))
            if location is not None and "{location}" in suf_path: suf_path = suf_path.replace("{location}", _slugify_for_path(location))
            if not path.endswith("/"): path += "/"
            path += suf_path.lstrip("/")

        if suf_query:
            if "{keyword}"  in suf_query and keyword  is not None: suf_query = suf_query.replace("{keyword}",  quote_plus(keyword))
            if "{location}" in suf_query and location is not None: suf_query = suf_query.replace("{location}", quote_plus(location))
            query.update(dict(parse_qsl(suf_query, keep_blank_values=True)))

    # 7) guard: if keyword slug already in PATH, drop any 'q'
    if keyword and _slugify_for_path(keyword) in path:
        query.pop("q", None)

    final = p._replace(path=path, query=urlencode(query, doseq=True))
    return urlunparse(final)
