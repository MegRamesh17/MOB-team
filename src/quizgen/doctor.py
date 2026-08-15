"""
Connectivity check for everything in .env.

Run this first after pasting keys in. It tells you which of the four services are
reachable and, crucially, what your *deployment names* actually are — the single most
common Azure OpenAI error is using the model name ("gpt-4o-mini") when the deployment
was created under a different name.

Prints only status and error types. Never prints a key.
"""

from __future__ import annotations

from typing import List, Tuple

from .config import CONFIG

OK = "  [ ok ]"
BAD = "  [FAIL]"
SKIP = "  [skip]"


def _mask(value: str) -> str:
    """Enough to confirm the right value is loaded, never enough to use."""
    if not value:
        return "(unset)"
    if len(value) < 10:
        return "(set, short)"
    return "{}...{}  ({} chars)".format(value[:4], value[-2:], len(value))


def check_config() -> List[str]:
    lines = [".env values loaded:"]
    lines.append("  chat endpoint      {}".format(CONFIG.azure_openai_endpoint or "(unset)"))
    lines.append("  chat key           {}".format(_mask(CONFIG.azure_openai_key)))
    lines.append("  chat deployment    {}".format(CONFIG.azure_chat_deployment))
    lines.append("  embed endpoint     {}".format(CONFIG.embedding_endpoint or "(unset)"))
    lines.append("  embed key          {}".format(_mask(CONFIG.embedding_key)))
    lines.append("  embed deployment   {}".format(CONFIG.embedding_deployment))
    lines.append("  search endpoint    {}".format(CONFIG.search_endpoint or "(unset)"))
    lines.append("  search key         {}".format(_mask(CONFIG.search_key)))
    lines.append("  search index       {}".format(CONFIG.search_index))
    lines.append("  blob connection    {}".format("(set)" if CONFIG.storage_connection_string else "(unset)"))
    return lines


def check_chat() -> Tuple[bool, str]:
    gaps = CONFIG.missing_for_azure()
    if gaps:
        return False, "not configured: {}".format(", ".join(gaps))
    try:
        from openai import AzureOpenAI

        client = AzureOpenAI(
            azure_endpoint=CONFIG.azure_openai_endpoint,
            api_key=CONFIG.azure_openai_key,
            api_version=CONFIG.azure_api_version,
        )
        from .llm.azure_openai import _chat_kwargs

        response = client.chat.completions.create(
            model=CONFIG.azure_chat_deployment,
            messages=[{"role": "user", "content": "Reply with the single word: ready"}],
            **_chat_kwargs(CONFIG.azure_chat_deployment, 64, 0.0),
        )
        reply = (response.choices[0].message.content or "").strip()
        return True, "deployment {!r} responded: {!r}".format(CONFIG.azure_chat_deployment, reply)
    except Exception as exc:  # noqa: BLE001
        return False, _explain(exc)


def check_embeddings() -> Tuple[bool, str]:
    gaps = CONFIG.missing_for_embeddings()
    if gaps:
        return False, "not configured: {}".format(", ".join(gaps))
    try:
        from openai import AzureOpenAI

        client = AzureOpenAI(
            azure_endpoint=CONFIG.embedding_endpoint,
            api_key=CONFIG.embedding_key,
            api_version=CONFIG.azure_api_version,
        )
        response = client.embeddings.create(
            model=CONFIG.embedding_deployment, input="connectivity check"
        )
        dims = len(response.data[0].embedding)
        return True, "deployment {!r} returned a {}-dimension vector".format(
            CONFIG.embedding_deployment, dims
        )
    except Exception as exc:  # noqa: BLE001
        return False, _explain(exc)


def check_search() -> Tuple[bool, str]:
    gaps = CONFIG.missing_for_search()
    if gaps:
        return False, "not configured: {}".format(", ".join(gaps))
    try:
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents.indexes import SearchIndexClient

        client = SearchIndexClient(
            endpoint=CONFIG.search_endpoint, credential=AzureKeyCredential(CONFIG.search_key)
        )
        names = [i for i in client.list_index_names()]
        if CONFIG.search_index in names:
            return True, "index {!r} exists".format(CONFIG.search_index)
        return True, "reachable; index {!r} does not exist yet (indexes: {})".format(
            CONFIG.search_index, ", ".join(names) or "none"
        )
    except Exception as exc:  # noqa: BLE001
        return False, _explain(exc)


def check_blob() -> Tuple[bool, str]:
    if not CONFIG.storage_connection_string:
        return False, "not configured: AZURE_STORAGE_CONNECTION_STRING"
    try:
        from azure.storage.blob import BlobServiceClient

        client = BlobServiceClient.from_connection_string(CONFIG.storage_connection_string)
        containers = [c.name for c in client.list_containers()]
        return True, "reachable; containers: {}".format(", ".join(containers) or "none")
    except Exception as exc:  # noqa: BLE001
        return False, _explain(exc)


def _explain(exc: Exception) -> str:
    """Turn the usual Azure failures into something actionable."""
    name = type(exc).__name__
    text = str(exc)

    if "DeploymentNotFound" in text or "404" in text:
        return ("{}: deployment not found. The deployment NAME in Azure is often not the "
                "model name — check it in the portal under your resource > Deployments."
                .format(name))
    if "401" in text or "Access denied" in text or "Unauthorized" in text:
        return "{}: key rejected. Wrong key, or key belongs to a different resource.".format(name)
    if "403" in text:
        return "{}: forbidden. Key is valid but lacks permission for this operation.".format(name)
    if "getaddrinfo" in text or "NameResolution" in text or "ConnectionError" in text:
        return "{}: endpoint unreachable. Check the URL is complete and correct.".format(name)
    return "{}: {}".format(name, text[:160])


def run() -> int:
    print("\n".join(check_config()))
    print()

    checks = [
        ("Azure OpenAI (chat)", check_chat),
        ("Azure OpenAI (embeddings)", check_embeddings),
        ("Azure AI Search", check_search),
        ("Blob Storage", check_blob),
    ]

    failures = 0
    for label, fn in checks:
        ok, detail = fn()
        if ok:
            print("{} {:<28} {}".format(OK, label, detail))
        elif detail.startswith("not configured"):
            print("{} {:<28} {}".format(SKIP, label, detail))
        else:
            print("{} {:<28} {}".format(BAD, label, detail))
            failures += 1

    print()
    if failures:
        print("{} service(s) configured but failing. Nothing is broken in the pipeline —".format(failures))
        print("these are credential/deployment issues in .env.")
    else:
        print("Everything configured is reachable.")
    print("\nThe pipeline runs with QUIZGEN_PROVIDER=mock regardless of any of this.")
    return 1 if failures else 0
