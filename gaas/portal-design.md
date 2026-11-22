# UX GitOps pour les serveurs de jeu — proposition

Le LabOps Portal existant peut devenir le point d'entrée. Son authentification
OIDC, ses vues Kubernetes/Argo CD et son widget de serveur de jeu sont déjà
présents. Le module d'écriture Git décrit ici reste à implémenter ; ce document
ne signifie pas qu'une interface ou une GitHub App a été déployée.

## Parcours

Une carte par serveur affiche le jeu, la version demandée, l'état demandé,
l'état observé, l'adresse de connexion et la dernière sauvegarde vérifiée.
Les actions principales sont **Démarrer**, **Mettre en veille** et
**Changer de version**. Le formulaire de création demande d'abord le jeu,
le nom et une version disponible ; les paramètres techniques viennent d'un
profil d'infrastructure versionné. Un aperçu du YAML et du diff reste accessible.

Après un clic, afficher des étapes distinctes : changement enregistré dans Git,
réconciliation Argo CD, opération du contrôleur, serveur prêt. Pour une mise en
veille : sauvegarde vérifiée puis libération de la passerelle si elle n'a plus
de consommateurs. Un commit réussi ne suffit pas à afficher « serveur prêt ».
Les étapes peuvent se chevaucher : la passerelle et la restauration peuvent
être préparées en parallèle.

Le portail lit le GameRoom observé avec `observedGeneration`, la révision Argo
et la révision Git de la commande. Il ne présente un état observé comme réponse
au dernier clic que si ces références correspondent. Les erreurs indiquent
l'étape concernée et proposent une nouvelle tentative explicite. Une reprise
après fermeture du navigateur retrouve l'opération dans Git et Kubernetes.

## Politique Git proposée, à choisir avant activation

Pour cet usage personnel, une branche opérationnelle suivie par une Application
Argo CD dédiée contient uniquement les GameRooms et leur Kustomization :

- Démarrer ou mettre en veille : commit direct limité à `spec.state`.
- Création et changement de version : PR générée par le portail, contrôles CI,
  puis fusion automatique si la politique du dépôt le permet ; une validation
  manuelle peut aussi être conservée.
- Suppression : distinguer la mise en veille de la suppression de l'objet.
  La conservation des mondes est assurée par le contrôleur ; une purge des
  données constitue un autre parcours, absent du MVP.

Toutes ces écritures ciblent la même branche opérationnelle. Éviter de tenir
une copie de `spec.state` sur une branche et une copie de la configuration sur
une autre. Le code du contrôleur et de Terraform reste sur leurs branches
habituelles. Une alternative est de faire passer toutes les actions par une PR
automatiquement fusionnée : elle préserve les règles de branche existantes,
au prix d'une étape CI supplémentaire à chaque clic.

## Backend du portail

Le navigateur appelle des opérations typées du serveur Go, par exemple
`setState(room, Running, expectedRevision)` ou
`setVersion(room, releaseKey, expectedRevision)`. Il n'envoie pas de commandes
shell et ne choisit ni dépôt, branche, chemin, Secret ou image arbitraire.
Le serveur relit le manifeste Git, valide le schéma et les allocations,
modifie uniquement les champs autorisés et construit le diff.

Les écritures utilisent un contrôle de concurrence sur la révision source.
Un conflit impose de relire et revoir le changement ; il ne doit jamais
écraser une modification extérieure. Les requêtes répétées retrouvent le
commit ou la PR existante via une clé d'opération persistée, plutôt que créer
des PR en double. La commande garde un lien vers le commit/PR et l'identité
OIDC qui l'a demandée, indépendamment de l'auteur technique du commit.

Une GitHub App installée sur le dépôt cible reçoit Contents en écriture et
Pull requests en écriture ; ses clés restent dans Bitwarden/Kubernetes Secret,
jamais dans le navigateur. Ses permissions GitHub sont à l'échelle du dépôt :
la restriction des chemins doit être appliquée par le backend. Un dépôt dédié
aux GameRooms permet une isolation plus forte. Les règles de branche restent
applicables ; leur éventuel contournement doit être explicitement configuré,
pas présumé. Aucun jeton humain généraliste n'est nécessaire au parcours.

L'écriture exige OIDC et un groupe propriétaire explicite même si le portail
permet aujourd'hui l'édition de disposition sans OIDC dans certains modes.
Conserver la protection contre les requêtes venant d'un autre site.
Le compte Kubernetes du portail lit uniquement GameRooms/status et les
Applications Argo nécessaires ; le contrôleur reste l'unique écrivain des
ressources d'exécution. Le portail n'a pas besoin de lire les Secrets.

## État et dépendances

| Élément | Source de vérité |
| --- | --- |
| Configuration et état souhaité du serveur | GameRoom dans Git |
| Avancement et état réel | Kubernetes, Argo CD et contrôleur |
| Monde et sauvegardes | PVC conservé et dépôt Restic chiffré |
| Ressources cloud connues de Terraform | Backend d'état protégé |

Le premier MVP utilise les versions du catalogue immuable et des ports déjà
réservés dans le profil de passerelle. Le module actuel remplace la VM quand
sa table de routage change : créer un nouveau serveur avec de nouveaux ports
pendant qu'un autre joue nécessite d'abord une gestion des routes à chaud.
L'interface doit exposer cette contrainte avant validation, sans promettre
une création sans interruption qui n'est pas implémentée.

Avant activation : terminer l'acceptance du contrôleur, l'adoption revue de la
production et la récupération du backend Terraform ; déclarer ensuite
l'Application Argo opérationnelle, la CI des manifests, l'installation GitHub
App et son entrée Bitwarden dans LabOps. Tester un clic, un conflit Git, un
échec de sauvegarde et la reprise après redémarrage avec un monde de test.

Références : [écriture des fichiers via l'API GitHub](https://docs.github.com/en/rest/repos/contents#create-or-update-file-contents),
[permissions GitHub App](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/choosing-permissions-for-a-github-app),
[santé des ressources Argo CD](https://argo-cd.readthedocs.io/en/stable/operator-manual/health/).
