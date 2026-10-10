FROM gradle:8.10.2-jdk21-alpine AS build
WORKDIR /workspace
COPY build.gradle settings.gradle ./
COPY src ./src
RUN gradle clean bootJar --no-daemon

FROM alpine:3.22 AS profiler
ARG TARGETARCH
ARG ASYNC_PROFILER_VERSION=4.5
RUN apk add --no-cache curl tar \
    && set -eux; \
       arch="${TARGETARCH:-}"; \
       if [ -z "$arch" ]; then arch="$(uname -m)"; fi; \
       case "$arch" in \
         amd64|x86_64) package_arch="x64" ;; \
         arm64|aarch64) package_arch="arm64" ;; \
         *) echo "Unsupported architecture: $arch" >&2; exit 1 ;; \
       esac; \
       curl -fsSL "https://github.com/async-profiler/async-profiler/releases/download/v${ASYNC_PROFILER_VERSION}/async-profiler-${ASYNC_PROFILER_VERSION}-linux-${package_arch}.tar.gz" \
         | tar -xz -C /opt; \
       mv "/opt/async-profiler-${ASYNC_PROFILER_VERSION}-linux-${package_arch}" /opt/async-profiler

FROM eclipse-temurin:21-jdk-alpine
WORKDIR /app
COPY --from=build /workspace/build/libs/*.jar app.jar
COPY --from=profiler /opt/async-profiler /opt/async-profiler
ENV PATH="/opt/async-profiler/bin:${PATH}"
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
