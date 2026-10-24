import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from fv.config import DIM, REGION, env, require_sandbox
from fv.vec import truncate_normalize

NAME = "fv-qwen"
# Instance pools, cheapest first: SageMaker falls back on InsufficientInstanceCapacity, which hit
# g6e.xlarge and g6.2xlarge on 2026-10-08. All have >= 24 GB GPU memory (fp16 8B weights ~16 GB)
# and endpoint quota >= 1 in the lab account. USD/h from results/prices.md and the Price List.
POOLS = ["ml.g6.2xlarge", "ml.g5.2xlarge", "ml.g6e.xlarge", "ml.g6.4xlarge"]
INSTANCE = "|".join(POOLS)  # for log lines only
BATCH = 16
TAGS = [{"Key": "project", "Value": "fast-and-vectorious"}]  # the fv-lab budget filters on this tag
INSTRUCTIONS = {  # English instructions, per the model card
    "so": "Given a Spanish programming question, retrieve answers that solve it",
    "ley": "Given a question about Chilean law, retrieve the legal articles that answer it",
}


def query_text(q: str, corpus: str) -> str:
    return f"Instruct: {INSTRUCTIONS[corpus]}\nQuery:{q}"


def _region() -> str:
    """FV_QWEN_REGION moves only the GPU endpoint (us-east-1 GPU capacity ran out on 2026-10-08); FV_TEI_IMAGE must match it."""
    return os.environ.get("FV_QWEN_REGION", REGION)


def _sm():
    return boto3.client("sagemaker", region_name=_region())


def endpoint_up() -> None:
    require_sandbox()  # starts GPU billing
    sm = _sm()
    try:
        sm.create_model(ModelName=NAME, ExecutionRoleArn=env("SAGEMAKER_ROLE_ARN"), Tags=TAGS, PrimaryContainer={
            "Image": env("TEI_IMAGE"), "Environment": {"HF_MODEL_ID": "Qwen/Qwen3-Embedding-8B", "DTYPE": "float16"}})
        sm.create_endpoint_config(EndpointConfigName=NAME, Tags=TAGS, ProductionVariants=[{
            "VariantName": "main", "ModelName": NAME, "InitialInstanceCount": 1,
            "InstancePools": [{"InstanceType": t, "Priority": i} for i, t in enumerate(POOLS, 1)],
            "VariantInstanceProvisionTimeoutInSeconds": 1800,
            "ContainerStartupHealthCheckTimeoutInSeconds": 1200}])
        sm.create_endpoint(EndpointName=NAME, EndpointConfigName=NAME, Tags=TAGS)
        _wait_in_service(sm)
    except BaseException:  # a failed or interrupted bring-up must not leave billable resources behind
        endpoint_down()
        raise


def _wait_in_service(sm, timeout_s: int = 3600) -> None:
    """Poll instead of the boto waiter so a Failed endpoint reports its FailureReason before cleanup deletes it."""
    for n in range(timeout_s // 30):
        d = sm.describe_endpoint(EndpointName=NAME)
        st = d.get("EndpointStatus")
        if st == "InService":
            print(f"{NAME} InService: {[v.get('InstancePools', v.get('CurrentInstanceCount')) for v in d.get('ProductionVariants', [])]}", flush=True)
            return
        if st == "Failed":
            raise RuntimeError(f"{NAME} {INSTANCE} failed: {d.get('FailureReason')}")
        if n % 2 == 0:
            print(f"{NAME} {INSTANCE}: {st} after {n * 30 // 60} min", flush=True)
        time.sleep(30)
    raise RuntimeError(f"{NAME} {INSTANCE} not InService after {timeout_s // 60} min")


def _not_found(e: ClientError) -> bool:
    return e.response["Error"]["Code"] == "ValidationException" and (
        "Could not find" in str(e) or "does not exist" in str(e))


def _wait_deletable(sm, timeout_s: int = 5400) -> None:
    """DeleteEndpoint fails on an in-progress endpoint; wait until it settles (or is gone)."""
    for _ in range(timeout_s // 30):
        try:
            st = sm.describe_endpoint(EndpointName=NAME).get("EndpointStatus")
        except ClientError as e:
            if _not_found(e):
                return
            raise
        if st not in ("Creating", "Updating", "SystemUpdating", "RollingBack", "Deleting"):
            return
        time.sleep(30)


def endpoint_down() -> None:
    require_sandbox()  # the endpoint bills until deleted
    sm = _sm()
    _wait_deletable(sm)
    first = None
    for call, kw in ((sm.delete_endpoint, {"EndpointName": NAME}), (sm.delete_endpoint_config, {"EndpointConfigName": NAME}),
                     (sm.delete_model, {"ModelName": NAME})):
        try:
            call(**kw)
        except ClientError as e:
            if not _not_found(e) and first is None:
                first = e  # keep going: the other deletes still have to run
    if first is not None:
        raise first


class QwenEmbedder:
    def __init__(self, workers: int = 4):
        self.workers = workers
        self.rt = boto3.client("sagemaker-runtime", region_name=_region(),
                               config=Config(retries={"mode": "adaptive", "max_attempts": 8}, read_timeout=300))

    def _invoke(self, texts):
        r = self.rt.invoke_endpoint(EndpointName=NAME, ContentType="application/json",
                                    Body=json.dumps({"inputs": texts, "truncate": True}))
        return json.loads(r["Body"].read())

    def __call__(self, texts, purpose, corpus):
        texts = [query_text(t, corpus) for t in texts] if purpose == "query" else list(texts)
        batches = [texts[i:i + BATCH] for i in range(0, len(texts), BATCH)]
        workers = getattr(self, "workers", 1)
        with ThreadPoolExecutor(workers) as ex:
            parts = list(ex.map(self._invoke, batches))
        return truncate_normalize([v for p in parts for v in p], DIM)
