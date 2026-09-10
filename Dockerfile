# XuanWu - chay web/server.py tren mot may chu that (vd Render.com).
#
# Vi sao can Dockerfile rieng thay vi de nen tang tu doan: engine/libxuanwu.so
# phai duoc BIEN DICH LAI tren dung kien truc may chu (khong the mang ban da
# bien dich tren macOS sang chay - xem CLAUDE.md), nen anh nay phai co san
# trinh bien dich C. Neu bien dich that bai vi ly do gi do, csrc/build.sh tu
# in loi va engine roi ve duong Python thuan (cham hon ~30 lan nhung van
# dung) - khong lam sap ung dung.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements-web.txt .
RUN pip install --no-cache-dir -r requirements-web.txt

COPY . .
RUN bash csrc/build.sh || true

# Render (va da so nen tang) tu cap PORT qua bien moi truong - server.py da
# doc dung bien nay (xem CONG trong web/server.py). 8000 chi la du phong khi
# chay thu cuc bo bang docker run ma khong truyen PORT.
ENV PORT=8000
EXPOSE 8000

CMD ["python3", "web/server.py"]
