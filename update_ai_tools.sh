echo "-----------------------------------"
echo "Updating AI command-line tools..."
echo "-----------------------------------"
echo "# claude code"
claude update
echo "# gemini cli"
npm upgrade -g @google/gemini-cli

echo "-----------------------------------"
echo "Updated versions:"
echo "-----------------------------------"
echo "# claude code"
claude --version
echo "# gemini cli"
gemini --version
