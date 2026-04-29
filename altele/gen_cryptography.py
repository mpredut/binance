python -c "
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption
import base64

private_key = Ed25519PrivateKey.generate()
public_key = private_key.public_key()

pub_bytes = public_key.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
priv_bytes = private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())

with open('ed25519_private.pem', 'wb') as f:
    f.write(priv_bytes)
with open('ed25519_public.pem', 'wb') as f:
    f.write(pub_bytes)
    
print('Chei generate: ed25519_private.pem si ed25519_public.pem')
print('Public key base64:')
print(base64.b64encode(pub_bytes).decode())
"

