// SPDX-License-Identifier: MIT

pragma solidity 0.8.18;

/// @dev ERC20Permit token for testing purposes

/// @notice Modern, minimalist, and gas-optimized ERC20 implementation
/// @author Solbase (https://github.com/Sol-DAO/solbase/blob/main/src/tokens/ERC20/ERC20.sol)
/// @dev Do not manually set balances without updating totalSupply, as the sum of all user balances must not exceed it.
abstract contract ERC20 {
    string public name;
    string public symbol;
    uint8 public immutable decimals;

    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    constructor(string memory _name, string memory _symbol, uint8 _decimals) {
        name = _name;
        symbol = _symbol;
        decimals = _decimals;
    }

    function approve(
        address spender,
        uint256 amount
    ) public virtual returns (bool) {
        allowance[msg.sender][spender] = amount;
        return true;
    }

    function transfer(
        address to,
        uint256 amount
    ) public virtual returns (bool) {
        balanceOf[msg.sender] -= amount;

        // Cannot overflow because the sum of all user balances can't exceed the max uint256 value.
        unchecked {
            balanceOf[to] += amount;
        }
        return true;
    }

    function transferFrom(
        address from,
        address to,
        uint256 amount
    ) public virtual returns (bool) {
        uint256 allowed = allowance[from][msg.sender]; // Saves gas for limited approvals.

        if (allowed != type(uint256).max)
            allowance[from][msg.sender] = allowed - amount;

        balanceOf[from] -= amount;

        // Cannot overflow because the sum of all user balances can't exceed the max uint256 value.
        unchecked {
            balanceOf[to] += amount;
        }
        return true;
    }

    function _mint(address to, uint256 amount) internal virtual {
        totalSupply += amount; // will revert on overflow
        balanceOf[to] += amount;
    }
}

/// @notice ERC20 + EIP-2612 implementation, including EIP712 logic.
/** @dev Solbase ERC20Permit implementation (https://github.com/Sol-DAO/solbase/blob/main/src/tokens/ERC20/extensions/ERC20Permit.sol)
 ** plus Solbase EIP712 implementation (https://github.com/Sol-DAO/solbase/blob/main/src/utils/EIP712.sol)*/
abstract contract ERC20Permit is ERC20 {
    /// @dev `keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)")`.
    bytes32 internal constant DOMAIN_TYPEHASH =
        0x8b73c3c69bb8fe3d512ecc4cf759cc79239f7b179b0ffacaa9a75d522b39400f;

    bytes32 public DOMAIN_SEPARATOR;

    bytes32 internal hashedDomainName;
    bytes32 internal hashedDomainVersion;
    uint256 internal initialChainId;

    /// @dev `keccak256("Permit(address owner,address spender,uint256 value,uint256 nonce,uint256 deadline)")`.
    bytes32 public constant PERMIT_TYPEHASH =
        0x6e71edae12b1b97f4d1f60370fef10105fa2faae0126114a169c64845d6126c9;

    mapping(address => uint256) public nonces;

    error PermitExpired();
    error InvalidSigner();

    constructor(
        string memory _name,
        string memory _symbol,
        string memory _version,
        uint8 _decimals
    ) ERC20(_name, _symbol, _decimals) {
        hashedDomainName = keccak256(bytes(_name));
        hashedDomainVersion = keccak256(bytes(_version));
        initialChainId = block.chainid;
        DOMAIN_SEPARATOR = _computeDomainSeparator();
    }

    function permit(
        address owner,
        address spender,
        uint256 value,
        uint256 deadline,
        uint8 v,
        bytes32 r,
        bytes32 s
    ) public virtual {
        if (block.timestamp > deadline) revert PermitExpired();

        // Unchecked because the only math done is incrementing the owner's nonce which cannot realistically overflow.
        unchecked {
            address recoveredAddress = ecrecover(
                _computeDigest(
                    keccak256(
                        abi.encode(
                            PERMIT_TYPEHASH,
                            owner,
                            spender,
                            value,
                            nonces[owner]++,
                            deadline
                        )
                    )
                ),
                v,
                r,
                s
            );

            if (recoveredAddress == address(0)) revert InvalidSigner();
            if (recoveredAddress != owner) revert InvalidSigner();
            allowance[recoveredAddress][spender] = value;
        }
    }

     function domainSeparator() public view virtual returns (bytes32) {
        if (block.chainid == initialChainId) {
            return DOMAIN_SEPARATOR;
        } else {
            return _computeDomainSeparator();
        }
    }

    function _computeDomainSeparator() internal view virtual returns (bytes32) {
        return
            keccak256(
                abi.encode(
                    DOMAIN_TYPEHASH,
                    hashedDomainName,
                    hashedDomainVersion,
                    block.chainid,
                    address(this)
                )
            );
    }

    function _computeDigest(
        bytes32 hashStruct
    ) internal view virtual returns (bytes32) {
        return
            keccak256(
                abi.encodePacked("\x19\x01", domainSeparator(), hashStruct)
            );
    }
}

/// @notice ERC20 test 18 decimal token contract with symbol "TEST"
/// @dev not burnable or mintable; ERC20Permit implemented
contract TestToken is ERC20Permit {
    string public constant TESTTOKEN_NAME = "Test Token";
    string public constant TESTTOKEN_SYMBOL = "TEST";
    string public constant TESTTOKEN_VERSION = "1";
    uint8 public constant TESTTOKEN_DECIMALS = 18;

    constructor()
        ERC20Permit(
            TESTTOKEN_NAME,
            TESTTOKEN_SYMBOL,
            TESTTOKEN_VERSION,
            TESTTOKEN_DECIMALS
        )
    {}

    /// @notice allows anyone to mint tokens to any address, for testing purposes
    /// @param to: address which which receive the minted 'amt' of tokens
    /// @param amt: amount of tokens, which can be any amount that doesn't cause the totalSupply to exceed type(uint256).max
    function mintToken(address to, uint256 amt) public {
        _mint(to, amt);
    }
}
