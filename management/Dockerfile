# Use an official Python runtime as a parent image
FROM python:3.10

ENV HOST 0.0.0.0
EXPOSE 8080

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set the working directory in the container
WORKDIR /management

# Copy the current directory contents into the container at /app
COPY . /management

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Run main.py when the container launches
CMD ["python", "main.py"]

