"""aquaticy -- ein KI-Rechercheagent fuer die Kommandozeile."""

__version__ = "9.5.17"

#: Der Name dieser Fassung. Er steht ueberall hinter der Versionsnummer, wo
#: Menschen sie lesen -- nicht in Paketangaben und Kennungen fuer Server.
__codename__ = "Lion"

#: So steht die Fassung in der Oberflaeche, im Terminal und in der README.
VERSION_LABEL = f"{__version__} {__codename__}"

__all__ = ["VERSION_LABEL", "__codename__", "__version__"]
