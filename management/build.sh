# $1 = local image
# $2 = gcloud image, <REGION>-docker.pkg.dev/<PROJECT_ID>/<FUNCTION_ID>/<NAME>:latest

docker build -t $1 . --platform linux/amd64
docker tag $1 $2
docker push $2

sleep 2m # wait for image to be available

gcloud run deploy --image $2 --platform managed