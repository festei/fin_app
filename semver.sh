 #!/bin/sh

echo 'Versioning the application'
git pull origin main
git checkout main
git fetch --tags

cz -nr 21 bump --yes

git branch --set-upstream-to=origin/main main
git push origin main:$GITHUB_REF

TAG=$(git describe --tags $(git rev-list --tags --max-count=1))
git push origin $TAG