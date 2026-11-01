# Lakmus — instructions pour Claude Code

## Le projet

Lakmus est un outil open source (CLI + librairie Python) et, plus tard, un hub web pour publier, partager et exécuter des **suites de tests métier** sur des modèles d'IA, des agents et des serveurs MCP. L'unité centrale est la suite de tests, pas le modèle. Ce dépôt contient la CLI et le moteur ; le hub vit dans un dépôt séparé (`lakmuslabs/hub`).

Je travaille avec JIJI, développeur full-stack senior. Échanges avec lui en français, directs et techniques. Tout ce qui est écrit dans le dépôt (code, commentaires, docs, messages de commit, sorties CLI) est en anglais.

## Sources de vérité

Lis-les avant toute tâche qui touche à leur périmètre :

- `docs/architecture/ARCHITECTURE.md` : modules, flux d'exécution, interfaces.
- `docs/spec/suite-format.md` : le contrat central (format des suites). Protégé par un hook : modifiable seulement avec un ADR en cours.
- `docs/adr/` : décisions prises. Ne jamais diverger silencieusement d'un ADR accepté.
- [CONVENTIONS.md](https://github.com/lakmuslabs/.github/blob/main/CONVENTIONS.md) : conventions communes à toute l'organisation (langue, commits, tags, CI, sécurité). Ce fichier ne les répète pas ; en cas de contradiction, CONVENTIONS fait foi.

## Méthode de travail (obligatoire)

1. Commence par un rationale court (3 à 5 lignes) et un plan.
2. Nouveau module, interface publique, format, commande CLI ou dépendance : applique le skill `architecture-first` (ou délègue à l'agent `architect`), écris l'ADR, puis **arrête-toi et attends la validation de JIJI**.
3. Écris les tests à partir de la spec ou de l'ADR (agent `test-writer` si utile).
4. Implémente le minimum qui fait passer les tests.
5. `make check` doit être vert avant de déclarer une tâche terminée.
6. Pour un changement de plus de ~200 lignes ou touchant `engine`, `targets` ou le format `run.json` : demande une revue à l'agent `code-reviewer`. Pour secrets, code tiers ou fichiers : `security-reviewer`.

## Stack et commandes

Python 3.11+, uv, Typer, Pydantic v2, httpx (async), Jinja2 (environnement sandboxé), PyYAML, pytest + pytest-asyncio + respx, ruff, mypy strict.

```bash
uv sync                      # installer
uv run pytest -q             # tests unitaires (sans réseau)
uv run pytest -q -m live     # tests live (clés API requises)
uv run ruff format . && uv run ruff check .
uv run mypy
make check                   # format + lint + types + tests
uv run lakmus --help
```

## Arborescence cible

```text
src/lakmus/
  cli/        # commandes Typer, aucune logique métier
  suite/      # chargement, modèles Pydantic du manifeste, validation
  engine/     # orchestration d'un run : rendu, exécution, cache, retries
  targets/    # protocole Target + adaptateurs (openai, anthropic, ollama, http, mcp...)
  scorers/    # scorers intégrés + décorateur + chargement des scorers de suite
  results/    # modèle run.json, agrégation, intervalles de confiance
  report/     # rapport HTML statique
  errors.py   # exceptions du domaine
tests/
suites/       # suites phares maintenues par le projet
docs/
```

## Conventions

- Typage strict ; tout format externe (manifeste, cas, run.json, réponses API) passe par un modèle Pydantic. Pas de `dict` non typé entre modules.
- I/O asynchrone dans `engine` et `targets`. Aucun appel bloquant.
- Retries et backoff dans le moteur, jamais dans les adaptateurs.
- Déterminisme : graine, horloge et aléatoire injectés, jamais globaux.
- Codes de sortie CLI : 0 succès, 1 seuil franchi, 2 erreur d'usage ou de validation, 3 erreur d'exécution.
- Tests unitaires sans réseau (respx). Tests réels marqués `@pytest.mark.live`.
- Commits au format Conventional Commits (`feat(engine): ...`).
- Une dépendance ajoutée doit être justifiée dans l'ADR ou la description de PR, et `uv.lock` est committé avec elle.
- Les tags sont cloisonnés : `v*` pour l'outil, `suite/<domain>/<name>/v*` pour une suite. Seul `v*` déclenche une publication.

## Interdits

- Écrire une clé API ou un secret où que ce soit ; les clés viennent de variables d'environnement nommées dans la config.
- Toucher aux fichiers `cases/holdout*` (bloqué par hook).
- Exécuter `git push`, publier sur PyPI ou créer une release sans demande explicite.
- Coder le hub (API web, front) dans ce dépôt.
- Ajouter des abstractions « pour plus tard » hors du périmètre de la version en cours.

## Périmètre actuel

Version 0.1 : format de suite 0.1, `init`, `validate`, `run`, adaptateurs `openai`, `anthropic`, `ollama`, `openai-compatible`, scorers de base, `run.json`, cache local. Voir `docs/PROMPTS.md` pour l'ordre des jalons.
