from uuid import uuid4

from app.domains.integrations.mailjet.reply_token import decode_reply_token, encode_reply_token


def test_reply_token_roundtrip() -> None:
    cid = uuid4()
    uid = uuid4()
    secret = "test-secret-key-for-hmac"
    token = encode_reply_token(cid, uid, secret)
    got = decode_reply_token(token, secret)
    assert got == (cid, uid)


def test_reply_token_rejects_tamper() -> None:
    cid = uuid4()
    uid = uuid4()
    token = encode_reply_token(cid, uid, "a")
    assert decode_reply_token(token, "b") is None
