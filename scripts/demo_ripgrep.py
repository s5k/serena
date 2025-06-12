from serena.tools.ripgrepy_search import RipGrepySearch

def main():
    rg = RipGrepySearch()
    path = "/Users/user/Documents/projects/fulltime/magento/2.4/project"
    result = rg.search("extends ", path=path, include_gitignore=False)

    print(f"{result}")

if __name__ == "__main__":
    main()