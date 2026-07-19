# Tea One Live Transcribe — on-device whisper.cpp transcription add-on.
#
# whisper.cpp is compiled natively (musl/aarch64, NEON) so it runs lean on the
# Home Assistant Green. faster-whisper is deliberately NOT used: its ctranslate2
# backend ships glibc-only wheels and won't install on Alpine/musl.
#
# Models are NOT baked into the image — they live on the persistent /share mount
# (see run.py: model_dir), so rebuilds stay fast and models survive them.
FROM alpine:3.21

ARG WHISPER_REF=master

# Runtime deps: python (control loop), ffmpeg (audio extract + silence trim),
# tzdata (local timestamps), libstdc++/libgomp (whisper.cpp static binary links
# these). Build deps are added in a virtual package and dropped afterwards.
RUN apk add --no-cache python3 ffmpeg tzdata libstdc++ libgomp \
 && apk add --no-cache --virtual .build build-base cmake git \
 && git clone --depth 1 --branch "${WHISPER_REF}" https://github.com/ggerganov/whisper.cpp /tmp/whisper.cpp \
 && cmake -S /tmp/whisper.cpp -B /tmp/whisper.cpp/build \
      -DCMAKE_BUILD_TYPE=Release \
      -DBUILD_SHARED_LIBS=OFF \
      -DWHISPER_BUILD_TESTS=OFF \
      -DWHISPER_BUILD_EXAMPLES=ON \
 && cmake --build /tmp/whisper.cpp/build -j"$(nproc)" --config Release \
 && install -m 0755 /tmp/whisper.cpp/build/bin/whisper-cli /usr/local/bin/whisper-cli \
 && rm -rf /tmp/whisper.cpp \
 && apk del .build

COPY run.py /run.py

CMD ["python3", "/run.py"]
