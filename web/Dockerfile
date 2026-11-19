# Go web server: login, market helper, tracker. Only needs the shared SQLite database.
FROM golang:1.25-alpine AS build

WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /koruxa-web .

FROM alpine:3

# Same uid as the worker image, so both can write to the shared /data volume.
RUN adduser -D -u 10001 app && mkdir /data && chown app /data

COPY --from=build /koruxa-web /usr/local/bin/koruxa-web

USER app

EXPOSE 8091

CMD ["koruxa-web"]
