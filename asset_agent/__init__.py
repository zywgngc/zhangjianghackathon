"""Public Python interface for the game asset sub-agent (Python 3.9+)."""

__version__ = "0.1.0"
__all__ = ["AssetAgent", "MaterialPortal", "ArtMaterialPortal", "ResearchMaterialPortal", "Catalog", "SearchEngine", "HTTPEmbeddingProvider", "import_pack"]


def __getattr__(name):
    # Lazy exports also allow independent use of ingestion and retrieval tools.
    if name == "AssetAgent":
        from .agent import AssetAgent
        return AssetAgent
    if name == "MaterialPortal":
        from .portal import MaterialPortal
        return MaterialPortal
    if name == "ArtMaterialPortal":
        from .portal import ArtMaterialPortal
        return ArtMaterialPortal
    if name == "ResearchMaterialPortal":
        from .research_portal import ResearchMaterialPortal
        return ResearchMaterialPortal
    if name == "Catalog":
        from .catalog import Catalog
        return Catalog
    if name == "SearchEngine":
        from .retrieval import SearchEngine
        return SearchEngine
    if name == "HTTPEmbeddingProvider":
        from .embeddings import HTTPEmbeddingProvider
        return HTTPEmbeddingProvider
    if name == "import_pack":
        from .ingest import import_pack
        return import_pack
    raise AttributeError(name)
