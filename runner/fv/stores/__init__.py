def get_store(name: str, corpus: str, model: str):
    if name == "ddb":
        from fv.stores.dynamo import DynamoStore
        return DynamoStore(corpus, model)
    if name == "s3v":
        from fv.stores.s3v import S3VectorsStore
        return S3VectorsStore(corpus, model)
    if name == "aoss":
        from fv.stores.aoss import AossStore
        return AossStore(corpus, model)
    if name == "aurora":
        from fv.stores.aurora import AuroraStore
        return AuroraStore(corpus, model)
    raise ValueError(f"unknown store {name}")
