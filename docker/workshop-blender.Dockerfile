# syntax=docker/dockerfile:1
#
# Ein Werkstatt-Abbild mit Blender -- fuer den Code-Modus, wenn Aquaticy ein
# 3D-Design erstellen, bearbeiten oder rendern soll.
#
# Das ist NICHT das Abbild, in dem Aquaticy selbst laeuft (siehe Dockerfile
# im Wurzelverzeichnis). Es ist das Abbild fuer die Werkstatt: die
# abgeschottete Wegwerf-Maschine, in der Aquaticy im Code-Modus Code
# ausfuehren darf (siehe aquaticy/sandbox.py). Das mitgelieferte
# Standardabbild (python:3.12-slim) bringt kein Blender mit -- absichtlich,
# denn Blender allein bringt hunderte Megabyte mit, die niemand braucht, der
# nur ein Python-Skript testen will.
#
# Bauen:
#   docker build -f docker/workshop-blender.Dockerfile -t aquaticy-workshop-blender:local .
#
# Benutzen (in der .env oder den Web-Einstellungen):
#   AQUATICY_VM_IMAGE=aquaticy-workshop-blender:local
#   AQUATICY_VM_SIZE=plus
#
# Warum "plus": Blender braucht mehr als einen Kern und ein Gigabyte, um in
# vertretbarer Zeit etwas zu rendern -- mit der Groesse "normal" laeuft ein
# Rendering leicht ins Zeitlimit oder in den Speicher.
FROM python:3.12-slim

# Blender selbst plus die Bibliotheken, die es zum headless Rendern braucht
# (Software-OpenGL ueber Mesa -- in der Werkstatt gibt es keine Grafikkarte).
# Kein Editor, kein Desktop: Aquaticy startet Blender ausschliesslich ueber
# `blender --background --python <skript>`, nie mit Oberflaeche.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        blender \
        libgl1 \
        libxi6 \
        libxrender1 \
        libxxf86vm1 \
        libxfixes3 \
        libxkbcommon0 \
        libsm6 \
    && rm -rf /var/lib/apt/lists/*

# Derselbe unprivilegierte Nutzer wie im Standardabbild -- die Werkstatt
# startet den Container ohnehin mit `--user`, aber ein Heimatverzeichnis
# muss da sein, sonst legt Blender seine Konfiguration nirgendwo ab.
RUN useradd --create-home --uid 1000 werkstatt
USER werkstatt
WORKDIR /work
