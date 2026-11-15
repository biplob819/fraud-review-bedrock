import pytest

import secret_scan

FAKES = {
    "anthropic key": "sk-ant-" + "a1B2" * 10,
    "openai key": "sk-proj-" + "Z9y8" * 10,
    "aws access key": "AKIA" + "ABCDEFGHIJKLMNOP",
    "github token": "ghp_" + "x" * 36,
    "pypi token": "pypi-" + "A" * 60,
    "private key": "-----BEGIN RSA " + "PRIVATE KEY-----",
}
MORE = {
    "github fine-grained token": "github_pat_" + "A1b2" * 15,
    "aws access key (session)": "ASIA" + "ABCDEFGHIJKLMNOP",
    "aws secret key": "aws_secret_access_key = " + "a/B+" * 10,
    "encrypted private key": "-----BEGIN ENCRYPTED " + "PRIVATE KEY-----",
    "dsa private key": "-----BEGIN DSA " + "PRIVATE KEY-----",
    "pgp private key": "-----BEGIN PGP " + "PRIVATE KEY BLOCK-----",
    # W0.1 verifier (SF-5): formats a Bedrock project is likely to leak
    "sts json secret": '{"SecretAccessKey": "' + "a/B+" * 10 + '"}',
    "aws session token": "AWS_SESSION_TOKEN=" + "IQoJb3JpZ2luX2Vj" * 8,
    "bedrock api key": "ABSK" + "QmVkcm9ja0FQSUtleS" * 3,
    "github token after underscore": "TOKEN_ghp_" + "x" * 36,
    "slack bot token": "xoxb-" + "1234567890-abcdefghij",
    # verifier pass 2
    "bedrock short-term key": "bedrock-api-key-" + "YmVkcm9jay5hbWF6b25hd3MuY29t" * 2,
    "bedrock bearer env": "AWS_BEARER_TOKEN_BEDROCK=" + "Zm9vYmFyYmF6cXV4" * 3,
    "secret in yaml": "SecretAccessKey: " + "a/B+" * 10,
    "session token in python repr": "{'SessionToken': '" + "IQoJb3JpZ2luX2Vj" * 8 + "'}",
    "sts text row": "CREDENTIALS\t"
    + "ASIA"
    + "ABCDEFGHIJKLMNOP"
    + "\t2026-01-01T00:00:00Z\t"
    + "a/B+" * 10,
    "kaggle json": '{"username":"someone","key":"' + "0123456789abcdef" * 2 + '"}',
    "slack app token": "xapp-1-" + "A0123456789-abcdefghij",
    "slack webhook": "https://hooks.slack.com/services/" + "T0000/B0000/abcdefXYZ123",
    # verifier pass 3
    "kaggle env": "KAGGLE_KEY=" + "0123456789abcdef" * 2,
    "kaggle export": 'export KAGGLE_KEY="' + "0123456789abcdef" * 2 + '"',
    "hyphenated yaml secret": "aws-secret-access-key: " + "a/B+" * 10,
    "hyphenated yaml session": "aws-session-token: " + "IQoJb3JpZ2luX2Vj" * 8,
    "imds token json": '"Token" : "' + "IQoJb3JpZ2luX2Vj" * 8 + '"',
    "terraform secret_key": 'secret_key = "' + "a/B+" * 10 + '"',
    "prefixed access key": "X_" + "AKIA" + "ABCDEFGHIJKLMNOP",
    "prefixed openai key": "x_" + "sk-proj-" + "Z9y8" * 10,
    "slack refresh token": "xoxe-1-" + "abcdefghij1234567890",
    "slack workflow url": "https://hooks.slack.com/workflows/" + "T0000/A0000/1234/abcdefXYZ123",
    # verifier pass 4
    "underscore pypi": "_" + "pypi-" + "A" * 60,
    "underscore github pat": "_" + "github_pat_" + "A1b2" * 15,
    "sts xml secret": "<SecretAccessKey>" + "a/B+" * 10 + "</SecretAccessKey>",
    "aws-secret-key yaml": "aws-secret-key: " + "a/B+" * 10,
}


def test_self_test_passes():
    assert secret_scan.self_test() == 0


@pytest.mark.parametrize("kind", sorted(FAKES))
def test_detects_each_kind(tmp_path, kind):
    f = tmp_path / "leak.txt"
    f.write_text(f"value = {FAKES[kind]}\n", encoding="utf-8")
    hits = secret_scan.scan([f])
    assert hits and kind in hits[0]


@pytest.mark.parametrize("label", sorted(MORE))
def test_detects_formats_found_by_the_verifier(tmp_path, label):
    f = tmp_path / ".env.example"
    f.write_text(f"{MORE[label]}\n", encoding="utf-8")
    assert secret_scan.scan([f]), label


def test_non_utf8_file_is_still_scanned(tmp_path):
    f = tmp_path / "latin1.txt"
    f.write_bytes(b"caf\xe9 " + FAKES["anthropic key"].encode() + b"\n")
    assert secret_scan.scan([f])


def test_scan_is_deterministic(tmp_path):
    for i, kind in enumerate(sorted(FAKES)):
        (tmp_path / f"f{i}.txt").write_text(FAKES[kind] + "\n", encoding="utf-8")
    files = sorted(tmp_path.iterdir())
    assert secret_scan.scan(files) == secret_scan.scan(files)
    assert len(secret_scan.scan(files)) == len(FAKES)


def test_env_var_reference_is_clean(tmp_path):
    f = tmp_path / "ok.py"
    f.write_text('key = os.environ["OPENAI_API_KEY"]\n', encoding="utf-8")
    assert secret_scan.scan([f]) == []


@pytest.mark.safety
def test_live_repo_has_no_secrets():
    assert secret_scan.scan(secret_scan.candidate_files(secret_scan.ROOT)) == []
