#!/usr/bin/env bash

#############################################
# Dotfiles Installation Script
# Supports: macOS, Ubuntu/Debian, Windows (WSL/Git Bash)
#############################################

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Dotfiles directory
DOTFILES_DIR="$HOME/dotfiles"

# Function to print colored messages
print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Detect OS
detect_os() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        OS="macos"
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        if [ -f /etc/debian_version ]; then
            OS="ubuntu"
        else
            OS="linux"
        fi
    elif [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "cygwin" ]] || [[ -n "$WSL_DISTRO_NAME" ]]; then
        OS="windows"
    else
        print_error "Unsupported operating system: $OSTYPE"
        exit 1
    fi
    print_info "Detected OS: $OS"
}

# Check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Install Homebrew (macOS)
install_homebrew() {
    if ! command_exists brew; then
        print_info "Installing Homebrew..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

        # Add Homebrew to PATH for Apple Silicon
        if [[ -d "/opt/homebrew/bin" ]]; then
            eval "$(/opt/homebrew/bin/brew shellenv)"
        fi
        print_success "Homebrew installed"
    else
        print_success "Homebrew already installed"
    fi
}

# Install APT packages (Ubuntu/Debian)
install_apt_packages() {
    print_info "Updating APT repositories..."
    sudo apt-get update

    print_info "Installing required packages..."
    sudo apt-get install -y \
        git \
        zsh \
        vim \
        neovim \
        tmux \
        curl \
        wget \
        build-essential \
        xsel

    print_success "APT packages installed"
}

# Install Homebrew packages (macOS)
install_brew_packages() {
    print_info "Installing Homebrew packages..."

    packages=(
        git
        zsh
        vim
        neovim
        tmux
        curl
        wget
    )

    for package in "${packages[@]}"; do
        if brew list "$package" &>/dev/null; then
            print_info "$package already installed"
        else
            brew install "$package"
        fi
    done

    print_success "Homebrew packages installed"
}

# Install WezTerm
install_wezterm() {
    if [[ "$OS" == "macos" ]]; then
        if ! brew list --cask wezterm &>/dev/null; then
            print_info "Installing WezTerm..."
            brew install --cask wezterm
            print_success "WezTerm installed"
        else
            print_success "WezTerm already installed"
        fi
    elif [[ "$OS" == "ubuntu" ]]; then
        if ! command_exists wezterm; then
            print_info "Installing WezTerm..."
            curl -fsSL https://apt.fury.io/wez/gpg.key | sudo gpg --yes --dearmor -o /usr/share/keyrings/wezterm-fury.gpg
            echo 'deb [signed-by=/usr/share/keyrings/wezterm-fury.gpg] https://apt.fury.io/wez/ * *' | sudo tee /etc/apt/sources.list.d/wezterm.list
            sudo apt-get update
            sudo apt-get install -y wezterm
            print_success "WezTerm installed"
        else
            print_success "WezTerm already installed"
        fi
    fi
}

# Install fonts
install_fonts() {
    if [[ "$OS" == "macos" ]]; then
        print_info "Installing fonts..."
        brew tap homebrew/cask-fonts
        brew install --cask font-ubuntu-mono
        brew install --cask font-hack-nerd-font
        print_success "Fonts installed"
    elif [[ "$OS" == "ubuntu" ]]; then
        print_info "Installing fonts..."
        sudo apt-get install -y fonts-ubuntu fonts-hack
        print_success "Fonts installed"
    fi
}

# Install Node.js and npm
install_nodejs() {
    if ! command_exists node; then
        print_info "Installing Node.js..."
        if [[ "$OS" == "macos" ]]; then
            brew install node
        elif [[ "$OS" == "ubuntu" ]]; then
            curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
            sudo apt-get install -y nodejs
        fi
        print_success "Node.js installed"
    else
        print_success "Node.js already installed ($(node --version))"
    fi

    # Setup npm global directory
    mkdir -p "$HOME/.npm-global"
    npm config set prefix "$HOME/.npm-global"
    print_success "npm global directory configured"
}

# Install Oh My Zsh
install_oh_my_zsh() {
    if [ ! -d "$HOME/.oh-my-zsh" ]; then
        print_info "Installing Oh My Zsh..."
        sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended
        print_success "Oh My Zsh installed"
    else
        print_success "Oh My Zsh already installed"
    fi

    # Install zsh plugins
    print_info "Installing zsh plugins..."

    # zsh-autosuggestions
    if [ ! -d "$HOME/.oh-my-zsh/custom/plugins/zsh-autosuggestions" ]; then
        git clone https://github.com/zsh-users/zsh-autosuggestions "$HOME/.oh-my-zsh/custom/plugins/zsh-autosuggestions"
    fi

    # zsh-syntax-highlighting
    if [ ! -d "$HOME/.oh-my-zsh/custom/plugins/zsh-syntax-highlighting" ]; then
        git clone https://github.com/zsh-users/zsh-syntax-highlighting "$HOME/.oh-my-zsh/custom/plugins/zsh-syntax-highlighting"
    fi

    print_success "zsh plugins installed"
}

# Install vim-plug
install_vim_plug() {
    print_info "Installing vim-plug..."

    # For Vim
    if [ ! -f "$HOME/.vim/autoload/plug.vim" ]; then
        curl -fLo "$HOME/.vim/autoload/plug.vim" --create-dirs \
            https://raw.githubusercontent.com/junegunn/vim-plug/master/plug.vim
    fi

    # For Neovim
    if [ ! -f "$HOME/.config/nvim/autoload/plug.vim" ]; then
        curl -fLo "$HOME/.config/nvim/autoload/plug.vim" --create-dirs \
            https://raw.githubusercontent.com/junegunn/vim-plug/master/plug.vim
    fi

    print_success "vim-plug installed"
}

# Create symbolic links
create_symlinks() {
    print_info "Creating symbolic links..."

    # Backup existing files
    backup_dir="$HOME/.dotfiles_backup_$(date +%Y%m%d_%H%M%S)"
    mkdir -p "$backup_dir"

    # Files to symlink
    files=(
        ".zshrc"
        ".vimrc"
        ".tmux.conf"
        ".gitconfig"
        ".gitignore_global"
        ".wezterm.lua"
    )

    for file in "${files[@]}"; do
        if [ -f "$HOME/$file" ] && [ ! -L "$HOME/$file" ]; then
            print_warning "Backing up existing $file"
            mv "$HOME/$file" "$backup_dir/"
        fi
        ln -sf "$DOTFILES_DIR/$file" "$HOME/$file"
        print_success "Linked $file"
    done

    # Directories to symlink
    mkdir -p "$HOME/.config"

    # Neovim config
    if [ -d "$HOME/.config/nvim" ] && [ ! -L "$HOME/.config/nvim" ]; then
        print_warning "Backing up existing nvim config"
        mv "$HOME/.config/nvim" "$backup_dir/"
    fi
    ln -sf "$DOTFILES_DIR/.config/nvim_lazy" "$HOME/.config/nvim"
    print_success "Linked Neovim config"

    # Claude Code config
    if [ -d "$HOME/.claude" ] && [ ! -L "$HOME/.claude" ]; then
        print_warning "Backing up existing Claude config"
        mv "$HOME/.claude" "$backup_dir/"
    fi
    ln -sf "$DOTFILES_DIR/.claude" "$HOME/.claude"
    print_success "Linked Claude config"

    # Codex config
    if [ -d "$HOME/.codex" ] && [ ! -L "$HOME/.codex" ]; then
        print_warning "Backing up existing Codex config"
        mv "$HOME/.codex" "$backup_dir/"
    fi
    ln -sf "$DOTFILES_DIR/.codex" "$HOME/.codex"
    print_success "Linked Codex config"

    # Gemini config
    if [ -d "$HOME/.gemini" ] && [ ! -L "$HOME/.gemini" ]; then
        print_warning "Backing up existing Gemini config"
        mv "$HOME/.gemini" "$backup_dir/"
    fi
    ln -sf "$DOTFILES_DIR/.gemini" "$HOME/.gemini"
    print_success "Linked Gemini config"

    # Oh My Zsh custom theme
    ln -sf "$DOTFILES_DIR/.oh-my-zsh/custom" "$HOME/.oh-my-zsh/custom"
    print_success "Linked Oh My Zsh custom theme"

    if [ "$(ls -A "$backup_dir" 2>/dev/null)" ]; then
        print_info "Backup created at: $backup_dir"
    else
        rmdir "$backup_dir"
    fi
}

# Install Vim plugins
install_vim_plugins() {
    print_info "Installing Vim plugins..."
    vim +PlugInstall +qall || true
    print_success "Vim plugins installed"
}

# Setup Neovim
setup_neovim() {
    print_info "Setting up Neovim..."
    print_info "Please open Neovim manually to complete lazy.nvim setup: nvim"
    print_info "Lazy.nvim will automatically install plugins on first launch"
}

# Install AI tools (optional)
install_ai_tools() {
    read -p "Do you want to install AI development tools? (y/n): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        print_info "Installing AI development tools..."

        # Claude Code
        if ! command_exists claude; then
            print_warning "Claude Code installation requires manual setup"
            print_info "Visit: https://docs.anthropic.com/claude-code"
        fi

        # Install npm-based tools
        npm install -g @openai/codex 2>/dev/null || print_warning "Failed to install Codex"
        npm install -g @google/gemini-cli 2>/dev/null || print_warning "Failed to install Gemini CLI"
        npm install -g @github/copilot 2>/dev/null || print_warning "Failed to install GitHub Copilot CLI"

        print_success "AI tools installation attempted (check warnings above)"
    fi
}

# Install linters and formatters (used by .claude/hooks/lint.sh and auto-format.sh)
install_linters_formatters() {
    print_info "Installing linters and formatters..."

    # --- npm-based tools (cross-platform, requires Node.js) ---
    if command_exists npm; then
        local npm_tools=(
            prettier   # JS/TS/JSON/CSS/HTML/MD/YAML formatter
            eslint     # JS/TS linter
            typescript # tsc type checker
        )
        for tool in "${npm_tools[@]}"; do
            if ! command_exists "$tool" && ! npm list -g "$tool" &>/dev/null; then
                print_info "Installing $tool via npm..."
                npm install -g "$tool" 2>/dev/null || print_warning "Failed to install $tool"
            else
                print_info "$tool already installed"
            fi
        done
    else
        print_warning "npm not found, skipping npm-based tools"
    fi

    # --- pip-based tools (cross-platform, requires Python) ---
    if command_exists pip3 || command_exists pip; then
        local pip_cmd="pip3"
        command_exists pip3 || pip_cmd="pip"

        local pip_tools=(
            ruff     # Python linter + formatter
            bandit   # Python security linter
            mypy     # Python type checker
            autopep8 # Python formatter (fallback)
            isort    # Python import sorter
        )
        for tool in "${pip_tools[@]}"; do
            if ! command_exists "$tool"; then
                print_info "Installing $tool via pip..."
                $pip_cmd install --user "$tool" 2>/dev/null || print_warning "Failed to install $tool"
            else
                print_info "$tool already installed"
            fi
        done
    else
        print_warning "pip not found, skipping Python tools"
    fi

    # --- Platform-specific tools ---
    case "$OS" in
    macos)
        local brew_tools=(
            shellcheck   # Shell script linter
            shfmt        # Shell script formatter
            cppcheck     # C/C++ linter
            clang-format # C/C++ formatter (part of llvm)
            staticcheck  # Go advanced linter
            checkstyle   # Java linter
            php-cs-fixer # PHP formatter
        )
        for tool in "${brew_tools[@]}"; do
            if ! brew list "$tool" &>/dev/null; then
                print_info "Installing $tool via brew..."
                brew install "$tool" 2>/dev/null || print_warning "Failed to install $tool"
            else
                print_info "$tool already installed"
            fi
        done

        # google-java-format (brew cask or manual)
        if ! command_exists google-java-format; then
            brew install google-java-format 2>/dev/null || print_warning "Failed to install google-java-format"
        fi

        # Go tools (requires go)
        if command_exists go; then
            if ! command_exists goimports; then
                print_info "Installing goimports..."
                go install golang.org/x/tools/cmd/goimports@latest 2>/dev/null || print_warning "Failed to install goimports"
            fi
        fi

        # Ruby tools
        if command_exists gem; then
            if ! command_exists rubocop; then
                print_info "Installing rubocop via gem..."
                gem install rubocop 2>/dev/null || print_warning "Failed to install rubocop"
            fi
        fi

        # PHP tools
        if command_exists php && ! command_exists phpstan; then
            if command_exists composer; then
                print_info "Installing phpstan via composer..."
                composer global require phpstan/phpstan 2>/dev/null || print_warning "Failed to install phpstan"
            fi
        fi
        ;;

    ubuntu)
        print_info "Installing APT-based linter/formatter packages..."
        sudo apt-get install -y \
            shellcheck \
            cppcheck \
            clang-format \
            2>/dev/null || print_warning "Some APT packages failed to install"

        # shfmt (snap or go install)
        if ! command_exists shfmt; then
            if command_exists snap; then
                print_info "Installing shfmt via snap..."
                sudo snap install shfmt 2>/dev/null || print_warning "Failed to install shfmt via snap"
            elif command_exists go; then
                print_info "Installing shfmt via go install..."
                go install mvdan.cc/sh/v3/cmd/shfmt@latest 2>/dev/null || print_warning "Failed to install shfmt"
            fi
        fi

        # staticcheck (go install)
        if command_exists go; then
            if ! command_exists staticcheck; then
                print_info "Installing staticcheck..."
                go install honnef.co/go/tools/cmd/staticcheck@latest 2>/dev/null || print_warning "Failed to install staticcheck"
            fi
            if ! command_exists goimports; then
                print_info "Installing goimports..."
                go install golang.org/x/tools/cmd/goimports@latest 2>/dev/null || print_warning "Failed to install goimports"
            fi
        fi

        # Ruby tools
        if command_exists gem; then
            if ! command_exists rubocop; then
                print_info "Installing rubocop via gem..."
                gem install rubocop 2>/dev/null || print_warning "Failed to install rubocop"
            fi
        fi

        # PHP tools
        if command_exists php && ! command_exists phpstan; then
            if command_exists composer; then
                print_info "Installing phpstan via composer..."
                composer global require phpstan/phpstan 2>/dev/null || print_warning "Failed to install phpstan"
            fi
        fi
        if ! command_exists php-cs-fixer; then
            if command_exists composer; then
                print_info "Installing php-cs-fixer via composer..."
                composer global require friendsofphp/php-cs-fixer 2>/dev/null || print_warning "Failed to install php-cs-fixer"
            fi
        fi

        # checkstyle / google-java-format (manual install if Java available)
        if command_exists java; then
            print_info "Java detected. checkstyle and google-java-format may need manual installation."
            print_info "  checkstyle: https://checkstyle.sourceforge.io/"
            print_info "  google-java-format: https://github.com/google/google-java-format"
        fi
        ;;

    windows)
        print_warning "Windows detected. Install the following tools manually or via package manager:"
        echo "  npm tools (cross-platform): prettier, eslint, typescript"
        echo "  pip tools (cross-platform): ruff, bandit, mypy, autopep8, isort"
        echo "  Shell: shellcheck (scoop install shellcheck), shfmt (scoop install shfmt)"
        echo "  C/C++: cppcheck (scoop install cppcheck), clang-format (via LLVM)"
        echo "  Go: staticcheck, goimports (go install)"
        echo "  Ruby: rubocop (gem install rubocop)"
        echo "  PHP: phpstan, php-cs-fixer (composer global require)"
        echo "  Java: checkstyle, google-java-format"

        # npm/pip tools are still installed above (cross-platform)
        # Try scoop if available
        if command_exists scoop; then
            print_info "Scoop detected, installing available tools..."
            local scoop_tools=(shellcheck shfmt cppcheck)
            for tool in "${scoop_tools[@]}"; do
                if ! command_exists "$tool"; then
                    scoop install "$tool" 2>/dev/null || print_warning "Failed to install $tool via scoop"
                fi
            done
        fi
        ;;
    esac

    print_success "Linters and formatters installation completed"
}

# Change default shell to zsh
change_shell() {
    if [ "$SHELL" != "$(which zsh)" ]; then
        print_info "Changing default shell to zsh..."
        if command_exists chsh; then
            chsh -s "$(which zsh)"
            print_success "Default shell changed to zsh (restart terminal to apply)"
        else
            print_warning "chsh command not found. Please change shell manually: chsh -s $(which zsh)"
        fi
    else
        print_success "Default shell is already zsh"
    fi
}

# Main installation flow
main() {
    echo "================================================"
    echo "  Dotfiles Installation Script"
    echo "================================================"
    echo

    detect_os

    # Platform-specific package installation
    case "$OS" in
    macos)
        install_homebrew
        install_brew_packages
        ;;
    ubuntu)
        install_apt_packages
        ;;
    windows)
        print_warning "Windows detected. Please ensure Git Bash or WSL is properly configured."
        print_warning "Some features may require manual installation."
        ;;
    esac

    # Common installations
    install_wezterm
    install_fonts
    install_nodejs
    install_linters_formatters
    install_oh_my_zsh
    install_vim_plug

    # Create symlinks
    create_symlinks

    # Setup editors
    install_vim_plugins
    setup_neovim

    # Optional AI tools
    install_ai_tools

    # Change shell
    change_shell

    echo
    echo "================================================"
    print_success "Installation completed!"
    echo "================================================"
    echo
    print_info "Next steps:"
    echo "  1. Restart your terminal"
    echo "  2. Open Neovim to complete lazy.nvim setup: nvim"
    echo "  3. Install AI tools if needed (see README.md)"
    echo "  4. Customize configurations as needed"
    echo
    print_info "Utility scripts:"
    echo "  - ./update_ai_tools.sh : Update all AI tools"
    echo "  - ./tmux_send_to_all_except_nvim.sh : Send commands to tmux panes"
    echo
}

# Run main function
main "$@"
