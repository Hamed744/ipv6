# Dockerfile for Render.com with IPv6 Rotation Setup

FROM python:3.9-bullseye

# Install necessary tools (iproute2 for 'ip' command)
RUN apt-get update && apt-get install -y iproute2 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY ./requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY ./app.py /app/
COPY ./startup.sh /app/

# Copy your frontend HTML file
# The comment is now on a separate line to avoid parsing issues.
COPY ./main.html /app/main.html

# Make the startup script executable
RUN chmod +x /app/startup.sh

# Run the startup script which configures IPs and then starts the app
CMD ["/app/startup.sh"]
