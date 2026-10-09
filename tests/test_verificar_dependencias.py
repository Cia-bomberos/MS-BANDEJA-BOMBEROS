import importlib.util
import json
import pathlib
import sys
from unittest.mock import MagicMock

from botocore.exceptions import ClientError

_DIR = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_DIR))
_SPEC = importlib.util.spec_from_file_location("verificar_dependencias", _DIR / "verificar_dependencias.py")
vd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(vd)


def test_policy_publica_solo_la_key_de_config():
    s3 = MagicMock()
    vd.abrir_lectura_publica_config(s3, "bomberos-f3-bandeja-config-qa")
    policy = json.loads(s3.put_bucket_policy.call_args.kwargs["Policy"])
    st = policy["Statement"][0]
    assert st["Action"] == "s3:GetObject"
    assert st["Resource"] == "arn:aws:s3:::bomberos-f3-bandeja-config-qa/bandeja-config.json"
    s3.put_bucket_cors.assert_called_once()


def test_si_aws_rechaza_no_rompe_el_deploy():
    s3 = MagicMock()
    s3.put_public_access_block.side_effect = ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "PutPublicAccessBlock")
    vd.abrir_lectura_publica_config(s3, "b")  # no debe lanzar
