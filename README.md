flowchart LR
  A[HiOrg\nQuelle / Source of Truth] -->|OAuth2\nAccess/Refresh Token| B[HiOrg-Sync\nFastAPI + UI]
  A -->|API: /personal\nFilter: updated_since| B

  B -->|Persistenz pro OV\nDATA_DIR/<ov>/\n- tokens.json\n- updated_since.txt\n- groupmap.json| S[(Storage)]

  B -->|LDAP/LDAPS\nUser Update/Sync\nGroup Memberships| C[Active Directory / LDAP\nZielsystem]
  C -->|Gruppen/Rollen/SSO\nVerzeichnisdaten| D[Systeme & Anwendungen\nFiles, Mail, VPN, Fachanwendungen]

  subgraph Admin
    U[Admin/IT] -->|UI: Gruppen-Mapping\nBaseDN + CN Overrides| B
    U -->|API-Key geschützt\n/sync/*| B
  end
