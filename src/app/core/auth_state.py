from app.core.security import TokenVerifier

_token_verifier: TokenVerifier | None = None


def set_token_verifier(verifier: TokenVerifier | None) -> None:
    global _token_verifier
    _token_verifier = verifier


def try_get_token_verifier() -> TokenVerifier | None:
    return _token_verifier


def get_token_verifier() -> TokenVerifier:
    verifier = try_get_token_verifier()
    if verifier is None:
        raise RuntimeError("Token verifier not initialized")
    return verifier

