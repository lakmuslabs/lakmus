# Lakmus — instructions de travail

## Le projet

Lakmus exécute des suites de tests métier contre des modèles, des agents et des serveurs MCP.
L'unité est la suite, pas le modèle. Ce dépôt contient la CLI et le moteur.

Interlocuteur : JIJI. Échanges en français, directs et techniques, sans préambule. Tout ce qui est
committé est en anglais : code, commentaires, documentation, messages de commit, sorties CLI.

## Sources de vérité

`docs/` n'est pas suivi par Git — référence locale, volontairement non publiée. Lis ce qui couvre
ton périmètre avant d'agir.

| Fichier | Contenu |
| --- | --- |
| `docs/spec/suite-format.md` | le contrat central. Hook : modifiable seulement avec un ADR en cours, et avec un bump de version de format |
| `docs/architecture/ARCHITECTURE.md` | modules, frontières, pipeline d'exécution, interfaces |
| `docs/adr/` | décisions actées. On s'écarte d'un ADR accepté en le remplaçant, jamais en l'ignorant |
| `docs/PROMPTS.md` | ordre des jalons v0.1 |

Les conventions communes à l'organisation — commits, tags, CI, posture sécurité — vivent dans
`lakmuslabs/.github`. Ce fichier ne les répète pas.

## Méthode

1. Rationale court et plan avant d'agir.
2. Nouveau module, interface publique, format, commande CLI ou dépendance : skill
   `architecture-first` ou agent `architect`, ADR écrit, puis **arrêt et validation de JIJI**.
3. Tests depuis la spec ou l'ADR, jamais depuis l'implémentation. Agent `test-writer` si utile.
4. Implémentation minimale qui fait passer les tests.
5. `make check` vert avant de déclarer une tâche terminée.
6. Au-delà de ~200 lignes, ou si ça touche `engine`, `targets` ou `run.json` : revue par l'agent
   `code-reviewer`. Secrets, code tiers, archives : `security-reviewer`.

## Stack et commandes

Python 3.11+, uv, Typer, Pydantic v2, httpx, Jinja2 sandboxé, PyYAML. Tests avec pytest,
pytest-asyncio et respx. Qualité avec ruff et mypy strict.

```bash
uv sync
make check                   # format, lint, types, tests
uv run pytest -q             # unitaires, sans réseau
uv run pytest -q -m live     # réels, clés requises
uv run lakmus --help
```

## Arborescence cible

```text
src/lakmus/
  cli/        # commandes Typer, aucune logique métier
  suite/      # chargement, modèles Pydantic du manifeste, validation
  engine/     # orchestration d'un run : rendu, exécution, cache, retries
  targets/    # protocole Target et adaptateurs
  scorers/    # scorers intégrés, décorateur, chargement des scorers de suite
  results/    # modèle run.json, agrégation, intervalles de confiance
  report/     # rapport HTML statique
  errors.py   # exceptions du domaine
tests/
suites/       # suites phares maintenues par le projet
```

`targets`, `scorers`, `suite` et `results` n'importent jamais `engine` ni `cli`.

## Règles de code

- Typage strict. Tout format externe — manifeste, cas, `run.json`, réponse d'API — passe par un
  modèle Pydantic. Aucun `dict` nu entre modules.
- `engine` et `targets` sont asynchrones. Un appel bloquant y est un bug.
- Retries, backoff et concurrence appartiennent au moteur. Un adaptateur lève et rend la main.
- Graine, horloge et aléatoire injectés, jamais globaux.
- Codes de sortie : 0 succès, 1 seuil franchi, 2 usage ou validation, 3 erreur d'exécution.
- Un commentaire explique un pourquoi non évident. Le reste se règle par un meilleur nom.
- Une dépendance ajoutée est justifiée dans l'ADR ou la PR, et `uv.lock` est committé avec elle.

## Interdits

- Écrire une clé ou un secret où que ce soit : elles viennent de variables d'environnement nommées
  dans la configuration.
- Toucher aux fichiers `cases/holdout*` — bloqué par hook.
- `git push`, tag, release ou publication PyPI sans demande explicite.
- Attribution IA dans un message de commit ou une description de PR.
- Coder le hub ici.
- Ajouter une abstraction hors du périmètre du jalon en cours.

## Périmètre v0.1

Format de suite 0.1, commandes `init`, `validate`, `run`, adaptateurs `openai`, `anthropic`,
`ollama`, `openai-compatible`, scorers de base, `run.json`, cache local.
