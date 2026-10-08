#!/usr/bin/env bash
# Create the first admin (an IAM engineer with the admin flag) from a one-time Secret, then delete the Secret.
#   ONB_ADMIN_USERNAME=admin ONB_ADMIN_PASSWORD=... make onboarding-bootstrap   (password prompted when unset)
source "$(dirname "$0")/lib.sh"
USERNAME="${ONB_ADMIN_USERNAME:-admin}"
if [[ -z "${ONB_ADMIN_PASSWORD:-}" ]]; then
  read -r -s -p "Password for $USERNAME (12+ characters): " ONB_ADMIN_PASSWORD; echo
fi
(( ${#ONB_ADMIN_PASSWORD} >= 12 )) || die "the password must be at least 12 characters"
kubectl -n "$ONB_NS" delete secret onboarding-bootstrap --ignore-not-found >/dev/null
kubectl -n "$ONB_NS" create secret generic onboarding-bootstrap --from-literal=ONB_ADMIN_USERNAME="$USERNAME" \
  --from-literal=ONB_ADMIN_DISPLAY_NAME="${ONB_ADMIN_DISPLAY_NAME:-Administrator}" \
  --from-file=ONB_ADMIN_PASSWORD=<(printf %s "$ONB_ADMIN_PASSWORD") >/dev/null
kubectl -n "$ONB_NS" delete job onboarding-bootstrap --ignore-not-found >/dev/null
kubectl apply -f - >/dev/null <<YAML
apiVersion: batch/v1
kind: Job
metadata: { name: onboarding-bootstrap, namespace: $ONB_NS }
spec:
  backoffLimit: 2
  ttlSecondsAfterFinished: 300
  template:
    metadata: { labels: { app: onboarding-bootstrap } }
    spec:
      restartPolicy: Never
      containers:
        - name: bootstrap
          image: $REGISTRY/onboarding-api:latest
          imagePullPolicy: Always
          command: ["python", "-m", "onboarding_api.bootstrap"]
          envFrom: [{ configMapRef: { name: onboarding-api } }, { secretRef: { name: onboarding-bootstrap } }]
YAML
kubectl -n "$ONB_NS" wait --for=condition=complete job/onboarding-bootstrap --timeout=180s >/dev/null \
  || { kubectl -n "$ONB_NS" logs job/onboarding-bootstrap; die "bootstrap failed"; }
kubectl -n "$ONB_NS" logs job/onboarding-bootstrap | tail -1
kubectl -n "$ONB_NS" delete secret onboarding-bootstrap >/dev/null
ok "one-time secret deleted; sign in as $USERNAME"
