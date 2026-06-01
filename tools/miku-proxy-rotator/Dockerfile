FROM golang:1.21-alpine AS builder

WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o rotator main.go

FROM alpine:latest
RUN apk --no-cache add ca-certificates
WORKDIR /root/
COPY --from=builder /app/rotator .

ENV PORT=1081
ENV BIND_INTERFACE=""
ENV BIND_IPS=""

EXPOSE 1081
CMD ["./rotator"]
