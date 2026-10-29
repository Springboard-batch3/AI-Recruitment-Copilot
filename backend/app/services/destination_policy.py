import ipaddress
import socket
import urllib.parse
from typing import List, Optional, Set
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.phase3_enums import ProviderEgressClass, ProviderType
from app.schemas.phase3_protocols import DestinationPolicy as DestinationPolicyProtocol


class DestinationPolicyResult(BaseModel):
    """Result of destination URL validation."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    is_valid: bool
    safe_error_code: Optional[str] = None
    safe_error_message: Optional[str] = None
    normalized_url: Optional[str] = None
    resolved_ips: List[str] = Field(default_factory=list)


class StrictDestinationPolicy(DestinationPolicyProtocol):
    """Production implementation of DestinationPolicy.
    
    Enforces:
    - Scheme validation (HTTP/HTTPS for local, HTTPS-only for remote);
    - Disallowance of credentials, fragments, and queries;
    - Ambiguous IP normalization and rejection of private/multicast/link-local/metadata IPs on remote routes;
    - Explicit loopback allowlists for local routes;
    - Explicit hostname allowlists for remote routes;
    - Full DNS prevalidation across IPv4 and IPv6.
    """

    DEFAULT_LOCAL_ALLOWED_PORTS: Set[int] = {1234, 8080, 11434}
    DEFAULT_LOCAL_ALLOWED_HOSTS: Set[str] = {"127.0.0.1", "::1", "localhost"}
    DEFAULT_APPLICATION_PORTS: Set[int] = {8000}

    def __init__(
        self,
        allow_remote: bool = False,
        remote_allowed_hosts: Optional[List[str]] = None,
        local_allowed_ports: Optional[Set[int]] = None,
        local_allowed_hosts: Optional[Set[str]] = None,
        application_ports: Optional[Set[int]] = None,
    ):
        self.allow_remote = allow_remote
        self.remote_allowed_hosts = {h.strip().lower() for h in (remote_allowed_hosts or []) if h.strip()}
        self.local_allowed_ports = local_allowed_ports or self.DEFAULT_LOCAL_ALLOWED_PORTS
        self.local_allowed_hosts = {h.strip().lower() for h in (local_allowed_hosts or self.DEFAULT_LOCAL_ALLOWED_HOSTS)}
        self.application_ports = application_ports or self.DEFAULT_APPLICATION_PORTS

    def validate_destination_url(self, target_url: str) -> bool:
        """Protocol method returning boolean validity."""
        result = self.evaluate_destination(target_url, egress_class=ProviderEgressClass.LOCAL_ONLY)
        return result.is_valid

    def evaluate_destination(
        self,
        target_url: str,
        egress_class: ProviderEgressClass = ProviderEgressClass.LOCAL_ONLY,
    ) -> DestinationPolicyResult:
        """Comprehensive URL evaluation returning structured diagnostic results."""
        if not target_url or not isinstance(target_url, str):
            return DestinationPolicyResult(
                is_valid=False,
                safe_error_code="INVALID_URL",
                safe_error_message="Destination URL must be a non-empty string.",
            )

        trimmed = target_url.strip()

        # 1. Parse URL using standard urllib.parse
        try:
            parsed = urllib.parse.urlsplit(trimmed)
        except Exception:
            return DestinationPolicyResult(
                is_valid=False,
                safe_error_code="MALFORMED_URL",
                safe_error_message="Destination URL could not be parsed.",
            )

        # 2. Scheme checks
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"}:
            return DestinationPolicyResult(
                is_valid=False,
                safe_error_code="UNSUPPORTED_SCHEME",
                safe_error_message=f"URL scheme '{parsed.scheme}' is unsupported. Only HTTP and HTTPS are permitted.",
            )

        # Remote providers MUST use HTTPS
        if egress_class == ProviderEgressClass.APPROVED_REMOTE and scheme != "https":
            return DestinationPolicyResult(
                is_valid=False,
                safe_error_code="REMOTE_HTTPS_REQUIRED",
                safe_error_message="Remote provider destinations must use secure HTTPS.",
            )

        # 3. Reject credentials in URL
        if parsed.username or parsed.password:
            return DestinationPolicyResult(
                is_valid=False,
                safe_error_code="EMBEDDED_CREDENTIALS_FORBIDDEN",
                safe_error_message="User credentials must not be embedded in destination URLs.",
            )

        # 4. Reject URL fragments
        if parsed.fragment:
            return DestinationPolicyResult(
                is_valid=False,
                safe_error_code="URL_FRAGMENT_FORBIDDEN",
                safe_error_message="Destination URLs must not contain fragment identifiers.",
            )

        # 5. Reject query parameters
        if parsed.query:
            return DestinationPolicyResult(
                is_valid=False,
                safe_error_code="URL_QUERY_FORBIDDEN",
                safe_error_message="Base destination URLs must not contain query parameters.",
            )

        # 6. Validate hostname
        hostname = parsed.hostname
        if not hostname:
            return DestinationPolicyResult(
                is_valid=False,
                safe_error_code="MISSING_HOSTNAME",
                safe_error_message="Destination URL lacks a valid hostname.",
            )
        hostname_lower = hostname.strip().lower()

        # 7. Evaluate Local vs Remote Policy
        if egress_class == ProviderEgressClass.LOCAL_ONLY:
            # Check allowed local hostnames
            if hostname_lower not in self.local_allowed_hosts:
                return DestinationPolicyResult(
                    is_valid=False,
                    safe_error_code="LOCAL_DESTINATION_FORBIDDEN",
                    safe_error_message=f"Host '{hostname}' is not an authorized local loopback destination.",
                )

            # Check port (default to 80 for http, 443 for https if not specified)
            port = parsed.port or (443 if scheme == "https" else 80)

            # Reject self-calls targeting the running FastAPI application server port
            if port in self.application_ports:
                return DestinationPolicyResult(
                    is_valid=False,
                    safe_error_code="APPLICATION_SELF_CALL_FORBIDDEN",
                    safe_error_message="This address points to the application server, not an AI provider.",
                )

            if port not in self.local_allowed_ports:
                return DestinationPolicyResult(
                    is_valid=False,
                    safe_error_code="LOCAL_PORT_UNAPPROVED",
                    safe_error_message=f"Local destination port {port} is not in the approved local ports list ({sorted(list(self.local_allowed_ports))}).",
                )

            # Resolve DNS to confirm it resolves strictly to loopback IP addresses
            resolved_ips = self._resolve_host(hostname_lower, port)
            if not resolved_ips:
                return DestinationPolicyResult(
                    is_valid=False,
                    safe_error_code="DNS_RESOLUTION_FAILED",
                    safe_error_message="Local destination hostname could not be resolved.",
                )

            for ip_str in resolved_ips:
                ip = ipaddress.ip_address(ip_str)
                if not ip.is_loopback:
                    return DestinationPolicyResult(
                        is_valid=False,
                        safe_error_code="LOCAL_DESTINATION_NON_LOOPBACK",
                        safe_error_message=f"Local host '{hostname}' resolved to non-loopback IP {ip_str}.",
                    )

            # Normalize URL path
            normalized = f"{scheme}://{hostname_lower}:{port}{parsed.path.rstrip('/')}"
            return DestinationPolicyResult(
                is_valid=True,
                normalized_url=normalized,
                resolved_ips=resolved_ips,
            )

        elif egress_class == ProviderEgressClass.APPROVED_REMOTE:
            # Remote egress must be explicitly enabled
            if not self.allow_remote:
                return DestinationPolicyResult(
                    is_valid=False,
                    safe_error_code="REMOTE_EGRESS_DISABLED",
                    safe_error_message="Remote model providers are disabled by platform policy.",
                )

            # Check exact allowlisted hostname
            if hostname_lower not in self.remote_allowed_hosts:
                return DestinationPolicyResult(
                    is_valid=False,
                    safe_error_code="REMOTE_HOST_NOT_ALLOWLISTED",
                    safe_error_message=f"Remote host '{hostname_lower}' is not in the server-configured allowlist.",
                )

            # Port check
            port = parsed.port or 443

            # Resolve DNS to ensure no private/metadata/loopback/link-local addresses
            resolved_ips = self._resolve_host(hostname_lower, port)
            if not resolved_ips:
                return DestinationPolicyResult(
                    is_valid=False,
                    safe_error_code="DNS_RESOLUTION_FAILED",
                    safe_error_message=f"Could not resolve remote host '{hostname_lower}'.",
                )

            for ip_str in resolved_ips:
                ip = ipaddress.ip_address(ip_str)
                if ip.is_private or ip.is_loopback or ip.is_multicast or ip.is_reserved or ip.is_link_local or ip.is_unspecified:
                    return DestinationPolicyResult(
                        is_valid=False,
                        safe_error_code="SSRF_PROHIBITED_IP",
                        safe_error_message=f"Remote host resolved to prohibited non-public IP address: {ip_str}.",
                    )
                # AWS / Cloud metadata check (169.254.169.254)
                if ip_str == "169.254.169.254":
                    return DestinationPolicyResult(
                        is_valid=False,
                        safe_error_code="SSRF_METADATA_IP",
                        safe_error_message="Access to cloud metadata endpoints is strictly blocked.",
                    )

            port_part = f":{port}" if port != 443 else ""
            normalized = f"https://{hostname_lower}{port_part}{parsed.path.rstrip('/')}"
            return DestinationPolicyResult(
                is_valid=True,
                normalized_url=normalized,
                resolved_ips=resolved_ips,
            )

        return DestinationPolicyResult(
            is_valid=False,
            safe_error_code="UNSUPPORTED_EGRESS_CLASS",
            safe_error_message=f"Egress class '{egress_class}' is unsupported.",
        )

    def _resolve_host(self, host: str, port: int) -> List[str]:
        """Resolves host using socket.getaddrinfo across AF_UNSPEC (IPv4 and IPv6)."""
        # If already an IP address, return immediately
        try:
            ip = ipaddress.ip_address(host)
            return [str(ip)]
        except ValueError:
            pass

        resolved: List[str] = []
        try:
            addr_info = socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
            for item in addr_info:
                sockaddr = item[4]
                ip_str = sockaddr[0]
                if ip_str not in resolved:
                    resolved.append(ip_str)
        except (socket.gaierror, socket.error):
            return []

        return resolved
