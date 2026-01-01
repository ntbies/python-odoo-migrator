# Copyright (c) 2024 ntbies OSS. MIT License.

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib import request
from urllib.error import URLError, HTTPError


class OdooRPCError(RuntimeError):
    pass


@dataclass
class OdooClient:
    url: str
    db: str
    username: str
    password: str
    # JSON-2 API (Odoo >= 20) optional API key
    api_key: Optional[str] = None
    # protocol: 'auto' (default), 'jsonrpc', or 'json2'
    protocol: str = "auto"
    uid: Optional[int] = None
    _resolved_protocol: Optional[str] = None
    _version_major: Optional[int] = None

    @property
    def endpoint(self) -> str:
        return self.url.rstrip('/') + '/jsonrpc'

    @property
    def json2_base(self) -> str:
        return self.url.rstrip('/') + '/json/2'

    @property
    def version(self) -> int:
        return self._version_major or 0

    def _detect_version_and_protocol(self) -> None:
        if self._resolved_protocol and self._version_major is not None:
            return
        if self.protocol in ("jsonrpc", "json2"):
            self._resolved_protocol = self.protocol
            return
        payload = json.dumps({"jsonrpc": "2.0", "params": {}}).encode('utf-8')
        info_url = self.url.rstrip('/') + '/web/webclient/version_info'
        try:
            req = request.Request(
                info_url,
                data=payload,
                headers={'Content-Type': 'application/json'},
                method='POST'
            )
            with request.urlopen(req) as resp:
                body = resp.read()
                data = json.loads(body.decode('utf-8')).get('result') or {}
                ver = data.get('server_version') or data.get('server_version_info')

                major = None
                if isinstance(ver, str):
                    # e.g., '19.0+e'
                    try:
                        major = int(ver.split('.')[0])
                    except Exception:
                        major = None
                elif isinstance(ver, (list, tuple)) and ver:
                    try:
                        major = int(ver[0])
                    except Exception:
                        major = None
                self._version_major = major
        except Exception as e:
            logging.getLogger(__name__).warning(f"Failed to detect Odoo version: {e}")
            self._version_major = None
        if self._version_major is not None and self._version_major >= 20:
            self._resolved_protocol = 'json2'
        else:
            self._resolved_protocol = 'jsonrpc'
        logging.getLogger(__name__).info(
            f"Detected protocol: {self._resolved_protocol} - Odoo version: {self._version_major or '?'}"
        )

    def _ensure_protocol(self) -> str:
        if not self._resolved_protocol:
            self._detect_version_and_protocol()
        return self._resolved_protocol or 'jsonrpc'

    def _post(self, payload: Dict[str, Any]) -> Any:
        data = json.dumps(payload).encode('utf-8')
        req = request.Request(self.endpoint, data=data, headers={'Content-Type': 'application/json'})
        with request.urlopen(req) as resp:
            body = resp.read()
            res = json.loads(body.decode('utf-8'))
        if 'error' in res:
            raise OdooRPCError(res['error'])
        return res.get('result')

    def _post_json2(self, model: str, method: str, body: Dict[str, Any]) -> Any:
        if not self.api_key:
            raise OdooRPCError('JSON-2 protocol selected but api_key is missing')
        url = f"{self.json2_base}/{model}/{method}"
        data = json.dumps(body).encode('utf-8')
        headers = {
            'Content-Type': 'application/json; charset=utf-8',
            'X-Odoo-Database': self.db,
            'Authorization': f"bearer {self.api_key}",
        }
        req = request.Request(url, data=data, headers=headers)
        try:
            with request.urlopen(req) as resp:
                resp_body = resp.read()
                return json.loads(resp_body.decode('utf-8'))
        except HTTPError as e:
            detail = e.read().decode('utf-8') if hasattr(e, 'read') else str(e)
            raise OdooRPCError(f"HTTP {e.code} on {url}: {detail}") from e
        except URLError as e:
            raise OdooRPCError(f"Connection error on {url}: {e}") from e

    def authenticate(self) -> int:
        proto = self._ensure_protocol()
        if proto == 'json2':
            if not self.api_key:
                raise OdooRPCError('api_key is required for JSON-2 (Odoo >= 19)')
            self.uid = 0
            return 0
        params = {
            'service': 'common',
            'method': 'authenticate',
            'args': [self.db, self.username, self.password, {}],
        }
        payload = {'jsonrpc': '2.0', 'method': 'call', 'params': params, 'id': 1}
        result = self._post(payload)
        if not isinstance(result, int):
            raise OdooRPCError(f'Authentication failed for {self.username}@{self.db}')
        self.uid = result
        return result

    def call_method(self, model: str, method: str, ids: List[int], **kwargs) -> Any:
        proto = self._ensure_protocol()
        if proto == 'json2':
            body: Dict[str, Any] = {"ids": ids or [], **kwargs}
            return self._post_json2(model, method, body)
        else:
            if ids:
                args = [ids]
            else:
                args = []
            return self.execute_kw(model, method, args, kwargs)

    def execute_kw(self, model: str, method: str, args: Optional[List[Any]] = None, kwargs: Optional[Dict[str, Any]] = None) -> Any:
        if model == "account.move" and self._version_major <= 12:
            model = "account.invoice"
        proto = self._ensure_protocol()
        if proto == 'json2':
            raise OdooRPCError('execute_kw is not supported with JSON-2 protocol')
        if self.uid is None:
            self.authenticate()
        args = args or []
        kwargs = kwargs or {}
        call_args = [self.db, self.uid, self.password, model, method, args, kwargs]
        params = {
            'service': 'object',
            'method': 'execute_kw',
            'args': call_args,
        }
        payload = {'jsonrpc': '2.0', 'method': 'call', 'params': params, 'id': 1}
        return self._post(payload)

    def fields_get(self, model: str, attributes: Optional[List[str]] = None) -> Dict[str, Dict[str, Any]]:
        attributes = attributes or ['type', 'store', 'required', 'string', 'relation']
        proto = self._ensure_protocol()
        if proto == 'json2':
            body: Dict[str, Any] = {"attributes": attributes}
            return self._post_json2(model, 'fields_get', body)
        return self.execute_kw(model, 'fields_get', [], {'attributes': attributes})

    def search(self, model: str, domain: Optional[List[Any]] = None, limit: Optional[int] = None, offset: int = 0, context: Optional[Dict] = None) -> List[int]:
        proto = self._ensure_protocol()
        ctx = context or {}
        if proto == 'json2':
            body: Dict[str, Any] = {"domain": domain or [], "context": ctx}
            if limit is not None:
                body['limit'] = limit
            if offset:
                body['offset'] = offset
            return self._post_json2(model, 'search', body)
        kwargs: Dict[str, Any] = {"context": ctx}
        if limit is not None:
            kwargs['limit'] = limit
        if offset:
            kwargs['offset'] = offset
        return self.execute_kw(model, 'search', [domain or []], kwargs)

    def read(self, model: str, ids: List[int], fields: Optional[List[str]] = None, context: Optional[Dict] = None) -> List[Dict[str, Any]]:
        proto = self._ensure_protocol()
        ctx = context or {}
        if proto == 'json2':
            body: Dict[str, Any] = {"ids": ids,"context": ctx}
            if fields:
                body['fields'] = fields
            return self._post_json2(model, 'read', body)
        kwargs: Dict[str, Any] = {"context": ctx}
        if fields:
            kwargs['fields'] = fields
        return self.execute_kw(model, 'read', [ids], kwargs)

    def search_read(self, model: str, domain: Optional[List[Any]] = None, fields: Optional[List[str]] = None, limit: Optional[int] = None, offset: int = 0, order_by='id asc', context: Optional[Dict] = None) -> List[Dict[str, Any]]:
        proto = self._ensure_protocol()
        ctx = context or {}
        if proto == 'json2':
            body: Dict[str, Any] = {"domain": domain or [], "context": ctx}
            if fields:
                body['fields'] = fields
            if limit is not None:
                body['limit'] = limit
            if offset:
                body['offset'] = offset
            if order_by:
                body['order'] = order_by
            return self._post_json2(model, 'search_read', body)
        kwargs: Dict[str, Any] = {"context": ctx}
        if fields:
            kwargs['fields'] = fields
        if limit is not None:
            kwargs['limit'] = limit
        if offset:
            kwargs['offset'] = offset
        if order_by:
            kwargs['order'] = order_by
        return self.execute_kw(model, 'search_read', [domain or []], kwargs)

    def name_search(self, model: str, name: str, operator: str = 'ilike', limit: int = 1) -> List[List[Any]]:
        proto = self._ensure_protocol()
        if proto == 'json2':
            body: Dict[str, Any] = {"name": name, "operator": operator, "limit": limit}
            return self._post_json2(model, 'name_search', body)
        return self.execute_kw(model, 'name_search', [name, []], {'operator': operator, 'limit': limit})

    def create(self, model: str, vals: Dict[str, Any], context: Optional[Dict] = None) -> int:
        ctx = context or {}
        proto = self._ensure_protocol()
        if proto == 'json2':
            body: Dict[str, Any] = {"vals_list": vals if isinstance(vals, list) else [vals], "context": ctx}
            result = self._post_json2(model, 'create', body)
            return result[0] if result else None
        return self.execute_kw(model, 'create', [vals], {'context': ctx})

    def write(self, model: str, ids: List[int], vals: Dict[str, Any]) -> bool:
        proto = self._ensure_protocol()
        if proto == 'json2':
            body: Dict[str, Any] = {"ids": ids, "vals": vals}
            return self._post_json2(model, 'write', body)
        return self.execute_kw(model, 'write', [ids, vals])
