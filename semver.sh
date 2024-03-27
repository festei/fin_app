 #!/bin/sh

echo 'Versioning the application'
'exists=`git show-ref refs/heads/test_cicd` && if [ -n "$exists" ]; then git branch -D test_cicd; fi'
git checkout -b test_cicd
cz bump --yes # execute auto bump and push to master
TAG=$(head -n 1 VERSION) # get the new software version and save into artifacts
echo "#!/bin/sh" >> variables
echo "export TAG='$TAG'" >> variables
