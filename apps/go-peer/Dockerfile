# syntax=docker/dockerfile:1
FROM golang:1.26.6-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/go-peer .

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/go-peer /usr/local/bin/go-peer
USER nonroot:nonroot
EXPOSE 4001 9100
ENTRYPOINT ["/usr/local/bin/go-peer"]
