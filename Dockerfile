# Production Dockerfile for Katha AI on Render
FROM node:20-bookworm-slim

# Install Python 3, pip, venv, and build tools
RUN apt-get update && apt-get install -y --no-install-recommends     python3     python3-pip     python3-venv     curl     && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Node dependencies
COPY package*.json ./
RUN npm install --omit=dev

# Install Python dependencies in a virtualenv
COPY requirements.txt ./
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip &&     pip install --no-cache-dir -r requirements.txt

# Copy repository files
COPY . .

# Ensure start script is executable
RUN chmod +x start.sh

# Default environment ports (Render overrides $PORT at runtime)
ENV PORT=8080
ENV NODE_PORT=3000
EXPOSE 8080

CMD ["./start.sh"]
